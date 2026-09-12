import io
import json
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementRequest, EnhancementReview
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.inspection import InspectionDecision
from pipe_inspector.contracts.tasks import CoverageSummary
from pipe_inspector.media import MediaStore
from pipe_inspector.vision.enhancement import run_enhancement
from pydantic import SecretStr
from test_enhancement import Model


@pytest.fixture
def paired(tmp_path):
    media = MediaStore(tmp_path / "assets")
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), "gray").save(stream, "PNG")
    stream.seek(0)
    asset = media.ingest(stream)
    result = run_enhancement(
        EnhancementRequest(
            input_asset=asset.ref,
            kind="super_resolution",
            roi=ImageRegion(width=32, height=24, bbox_xyxy=(2, 3, 20, 22)),
        ),
        media,
        Model("super_resolution", scale=4),
    )
    task_id = uuid4()
    finding = Finding(
        task_id=task_id,
        finding_id=uuid4(),
        canonical_label="BX",
        score=0.95,
        source="detector",
        review_status="confirmed",
        confirmed_by="rules",
        evidence=(
            EvidenceRef(
                asset=asset.ref, region=ImageRegion(width=32, height=24, bbox_xyxy=(2, 3, 18, 20))
            ),
        ),
    )
    baseline = InspectionDecision(
        findings=(finding,),
        inspection_result="defects_found",
        needs_review=False,
        reasons=(),
        coverage=CoverageSummary(
            sufficient_coverage=True, sufficient_quality=True, negative_audit_complete=True
        ),
    )
    return media, result, baseline


def test_auxiliary_conflict_never_relabels_removes_or_moves_original_finding(paired):
    from pipe_inspector.agent.enhancement_fusion import apply_enhancement_review

    _, result, baseline = paired
    original = baseline.findings[0]
    review = EnhancementReview(
        quality_effect="improved",
        summary="增强可能改变边缘",
        conflicting_finding_ids=(original.finding_id,),
    )
    decision = apply_enhancement_review(baseline, result, review)
    retained = decision.findings[0]
    assert retained.canonical_label == "BX" and retained.score == 0.95
    assert retained.evidence == original.evidence
    assert retained.finding_id == original.finding_id
    assert retained.review_status == "needs_review"
    assert decision.needs_review
    assert baseline.findings[0].review_status == "confirmed"


def test_new_anomaly_is_pending_and_quality_improvement_cannot_complete_coverage(paired):
    from pipe_inspector.agent.enhancement_fusion import apply_enhancement_review

    _, result, baseline = paired
    baseline = InspectionDecision.model_validate(
        baseline.model_dump()
        | {
            "coverage": CoverageSummary(),
            "findings": (),
            "inspection_result": "inconclusive",
        }
    )
    review = EnhancementReview(
        quality_effect="improved",
        summary="疑似遗漏，待人工核验",
        additional_anomalies=({"label": "PL", "reason": "裂纹形态", "bbox_xyxy": (4, 5, 10, 12)},),
    )
    decision = apply_enhancement_review(baseline, result, review, task_id=uuid4())
    assert decision.findings[0].review_status == "needs_review"
    assert decision.findings[0].evidence[0].asset == result.input_asset
    assert decision.coverage == baseline.coverage
    assert decision.inspection_result == "inconclusive"


def test_missing_auxiliary_review_is_failure_not_normal_or_success(paired):
    from pipe_inspector.agent.enhancement_fusion import apply_enhancement_review

    _, result, baseline = paired
    baseline = InspectionDecision.model_validate(
        baseline.model_dump()
        | {
            "findings": (),
            "inspection_result": "normal",
        }
    )
    decision = apply_enhancement_review(baseline, result, None)
    assert decision.inspection_result == "inconclusive"
    assert decision.needs_review and "enhancement_review_missing" in decision.reasons


def test_unlocalized_anomaly_does_not_turn_processing_roi_into_a_defect_box(paired):
    from pipe_inspector.agent.enhancement_fusion import apply_enhancement_review

    _, result, baseline = paired
    review = EnhancementReview(
        quality_effect="uncertain",
        summary="不能定位",
        additional_anomalies=({"label": "PL", "reason": "需人工定位", "bbox_xyxy": None},),
    )
    decision = apply_enhancement_review(baseline, result, review)
    assert decision.findings[-1].evidence[0].asset == result.input_asset
    assert decision.findings[-1].evidence[0].region is None


@pytest.mark.asyncio
async def test_auxiliary_request_contains_original_derived_schema_and_original_boxes(paired):
    from pipe_inspector.agent.enhancement_review import EnhancementReviewer

    media, result, baseline = paired
    captured = []

    def handler(request):
        captured.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "quality_effect": "unchanged",
                                    "summary": "无法证明新增真实细节",
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    reviewer = EnhancementReviewer(
        OpenAICompatibleClient(
            "https://example.invalid/v1",
            "fixture",
            SecretStr("fixture"),
            transport=httpx.MockTransport(handler),
        )
    )
    review = await reviewer.review(
        media.path_for(result.input_asset),
        media.path_for(result.output_asset.ref),
        result,
        baseline,
    )
    assert review.quality_effect == "unchanged"
    content = captured[0]["messages"][1]["content"]
    images = [item for item in content if item["type"] == "image_url"]
    assert len(images) == 2
    assert images[0]["image_url"]["url"] != images[1]["image_url"]["url"]
    metadata = json.loads(content[0]["text"])
    assert metadata["source_region"]["bbox_xyxy"] == [2, 3, 20, 22]
    assert metadata["findings"][0]["score"] == 0.95
    assert metadata["findings"][0]["canonical_label"] == "BX"
    assert set(metadata["label_guidance"]) == {"AJ", "BX", "FS", "PL", "BLOCK"}
    assert "conflicting_finding_ids" in metadata["response_schema"]["properties"]


@pytest.mark.parametrize("bad", ["id", "outside_roi"])
def test_auxiliary_response_cannot_reference_unknown_findings_or_outside_roi(paired, bad):
    from pipe_inspector.agent.enhancement_review import validate_enhancement_review

    _, result, baseline = paired
    changes = (
        {"conflicting_finding_ids": (uuid4(),)}
        if bad == "id"
        else {
            "additional_anomalies": (
                {"label": "PL", "reason": "裂纹", "bbox_xyxy": (0, 0, 31, 23)},
            )
        }
    )
    with pytest.raises(APIClientError):
        validate_enhancement_review(
            EnhancementReview(quality_effect="uncertain", summary="待核验", **changes),
            result,
            baseline,
        )
