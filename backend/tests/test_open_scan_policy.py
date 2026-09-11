from uuid import UUID

import pytest
from pipe_inspector.agent.open_scan import merge_open_candidates
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.findings import (
    EvidenceRef,
    Finding,
    FindingSource,
    ReviewStatus,
)
from pipe_inspector.contracts.inspection import InspectionDecision
from pipe_inspector.contracts.open_scan import (
    OPEN_SCAN_VERSION,
    OPEN_VOCABULARY_VERSION,
    OpenCandidate,
    OpenDetectionBatch,
    OpenScanEvidence,
)
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult
from pydantic import ValidationError

TASK_ID = UUID("00000000-0000-4000-8000-000000000201")
ASSET_ID = UUID("00000000-0000-4000-8000-000000000202")
OTHER_ASSET_ID = UUID("00000000-0000-4000-8000-000000000203")


def candidate(
    label: DetectionLabel | str = DetectionLabel.AJ,
    *,
    name: str = "joint displacement",
    score: float = 0.72,
    bbox: tuple[float, float, float, float] = (2, 3, 20, 30),
) -> OpenCandidate:
    return OpenCandidate(label=label, name=name, score=score, bbox_xyxy=bbox)


def scan(
    *candidates: OpenCandidate,
    asset_id: UUID = ASSET_ID,
    asset_version: int = 1,
    frame_index: int | None = None,
    timestamp_seconds: float | None = None,
) -> OpenScanEvidence:
    return OpenScanEvidence(
        batch=OpenDetectionBatch(
            detector_id="open-fixture",
            model_version="open-model-v1",
            vocabulary_version=OPEN_VOCABULARY_VERSION,
            width=64,
            height=48,
            candidates=candidates,
        ),
        asset=AssetRef(asset_id=asset_id, version=asset_version),
        frame_index=frame_index,
        timestamp_seconds=timestamp_seconds,
    )


def finding(
    *,
    finding_id: int = 1,
    label: DetectionLabel | str = DetectionLabel.AJ,
    score: float = 0.99,
    bbox: tuple[float, float, float, float] = (0, 0, 10, 10),
    status: ReviewStatus = ReviewStatus.CONFIRMED,
    source: FindingSource = FindingSource.DETECTOR,
    asset_id: UUID = ASSET_ID,
    asset_version: int = 1,
    frame_index: int | None = None,
    timestamp_seconds: float | None = None,
) -> Finding:
    return Finding(
        finding_id=UUID(f"00000000-0000-4000-8000-{finding_id:012d}"),
        task_id=TASK_ID,
        canonical_label=label,
        review_status=status,
        source=source,
        score=score,
        evidence=(
            EvidenceRef(
                asset=AssetRef(asset_id=asset_id, version=asset_version),
                region=ImageRegion(width=64, height=48, bbox_xyxy=bbox),
                frame_index=frame_index,
                timestamp_seconds=timestamp_seconds,
            ),
        ),
        confirmed_by="rules" if status is ReviewStatus.CONFIRMED else None,
    )


def decision(
    *findings: Finding,
    result: InspectionResult = InspectionResult.NORMAL,
    negative_audit_complete: bool = True,
    needs_review: bool = False,
    reasons: tuple[str, ...] = (),
) -> InspectionDecision:
    return InspectionDecision(
        findings=findings,
        inspection_result=result,
        coverage=CoverageSummary(
            sufficient_coverage=True,
            sufficient_quality=True,
            negative_audit_complete=negative_audit_complete,
        ),
        needs_review=needs_review,
        reasons=reasons,
    )


def test_open_contract_roundtrip_preserves_versions_and_typed_candidates() -> None:
    evidence = scan(
        candidate(DetectionLabel.BX, name="pipe deformation"),
        candidate("OTHER", name="root intrusion", bbox=(22, 4, 40, 32)),
    )

    restored = OpenScanEvidence.model_validate_json(evidence.model_dump_json())

    assert OPEN_SCAN_VERSION == "open-scan-v1"
    assert OPEN_VOCABULARY_VERSION == "sewer-open-v1"
    assert restored == evidence
    assert restored.batch.candidates[0].label is DetectionLabel.BX
    assert restored.batch.candidates[1].label == "OTHER"


def test_distinct_named_other_candidates_never_collapse_into_one():
    source = scan(
        candidate("OTHER", name="root intrusion"), candidate("OTHER", name="water infiltration")
    )
    result = merge_open_candidates(decision(), task_id=TASK_ID, scans=[source])
    assert [f.open_label for f in result.findings] == ["root intrusion", "water infiltration"]
    assert merge_open_candidates(result, task_id=TASK_ID, scans=[source]) == result


@pytest.mark.parametrize(
    "changes",
    [
        {"label": "NORMAL"},
        {"name": "   "},
        {"score": -0.01},
        {"score": 1.01},
        {"score": float("nan")},
        {"bbox_xyxy": (0, 0, float("inf"), 10)},
    ],
)
def test_open_candidate_rejects_unbounded_labels_names_scores_and_coordinates(changes) -> None:
    data = {
        "label": "AJ",
        "name": "joint displacement",
        "score": 0.5,
        "bbox_xyxy": (1, 2, 10, 20),
    }

    with pytest.raises(ValidationError):
        OpenCandidate.model_validate(data | changes)


@pytest.mark.parametrize(
    "bbox",
    [
        (-1, 0, 10, 10),
        (0, 0, 65, 10),
        (0, 0, 10, 49),
        (10, 0, 10, 10),
        (0, 10, 10, 10),
    ],
)
def test_open_batch_rejects_candidates_outside_original_image(bbox) -> None:
    with pytest.raises(ValidationError, match="bbox coordinates"):
        OpenDetectionBatch(
            detector_id="open-fixture",
            model_version="v1",
            vocabulary_version="vocab-v1",
            width=64,
            height=48,
            candidates=(candidate(bbox=bbox),),
        )


def test_open_batch_caps_candidates_at_one_hundred() -> None:
    common = {
        "detector_id": "open-fixture",
        "model_version": "v1",
        "vocabulary_version": "vocab-v1",
        "width": 64,
        "height": 48,
    }

    accepted = OpenDetectionBatch(**common, candidates=(candidate(),) * 100)
    assert len(accepted.candidates) == 100
    with pytest.raises(ValidationError):
        OpenDetectionBatch(**common, candidates=(candidate(),) * 101)


@pytest.mark.parametrize(
    ("frame_index", "timestamp_seconds"),
    [(0, None), (None, 0.0), (-1, 0.0), (0, -0.1), (0, float("inf"))],
)
def test_open_evidence_rejects_incomplete_or_invalid_video_locators(
    frame_index, timestamp_seconds
) -> None:
    with pytest.raises(ValidationError):
        scan(frame_index=frame_index, timestamp_seconds=timestamp_seconds)


def test_empty_open_scan_is_a_noop_and_does_not_mutate_input() -> None:
    original = decision(reasons=("existing_reason",))
    before = original.model_dump(mode="json")

    merged = merge_open_candidates(original, task_id=TASK_ID, scans=(scan(),))

    assert merged == original
    assert original.model_dump(mode="json") == before


def test_new_candidate_is_appended_for_review_and_downgrades_only_normal_result() -> None:
    original = decision()

    merged = merge_open_candidates(
        original,
        task_id=TASK_ID,
        scans=(scan(candidate(DetectionLabel.PL, name="longitudinal crack")),),
    )

    assert original.findings == ()
    assert len(merged.findings) == 1
    added = merged.findings[0]
    assert added.finding_id.version == 5
    assert added.task_id == TASK_ID
    assert added.canonical_label is DetectionLabel.PL
    assert added.open_label is None
    assert added.score == 0.72
    assert added.source is FindingSource.OPEN_DETECTOR
    assert added.review_status is ReviewStatus.NEEDS_REVIEW
    assert added.confirmed_by is None
    assert added.evidence[0].asset == AssetRef(asset_id=ASSET_ID, version=1)
    assert added.evidence[0].region == ImageRegion(width=64, height=48, bbox_xyxy=(2, 3, 20, 30))
    assert merged.inspection_result is InspectionResult.INCONCLUSIVE
    assert merged.needs_review is True
    assert merged.coverage == original.coverage


def test_other_candidate_keeps_its_name_and_cannot_be_auto_confirmed() -> None:
    merged = merge_open_candidates(
        decision(),
        task_id=TASK_ID,
        scans=(scan(candidate("OTHER", name="root intrusion")),),
    )

    added = merged.findings[0]
    assert added.canonical_label == "OTHER"
    assert added.open_label == "root intrusion"
    assert added.review_status is ReviewStatus.NEEDS_REVIEW
    assert added.confirmed_by is None


@pytest.mark.parametrize(
    "status",
    [ReviewStatus.TENTATIVE, ReviewStatus.NEEDS_REVIEW, ReviewStatus.CONFIRMED],
)
def test_same_asset_version_frame_class_at_half_iou_dedupes_non_rejected_finding(
    status: ReviewStatus,
) -> None:
    old = finding(
        status=status,
        frame_index=7,
        timestamp_seconds=1.4,
        bbox=(0, 0, 10, 10),
    )
    original = decision(
        old,
        result=(
            InspectionResult.DEFECTS_FOUND
            if status is ReviewStatus.CONFIRMED
            else InspectionResult.INCONCLUSIVE
        ),
        needs_review=status is not ReviewStatus.CONFIRMED,
    )

    merged = merge_open_candidates(
        original,
        task_id=TASK_ID,
        scans=(
            scan(
                candidate(bbox=(0, 0, 5, 10)),
                frame_index=7,
                timestamp_seconds=1.4,
            ),
        ),
    )

    assert merged == original
    assert merged.findings[0] is old


def test_rejected_finding_never_suppresses_a_new_open_candidate() -> None:
    rejected = finding(status=ReviewStatus.REJECTED)
    original = decision(rejected)

    merged = merge_open_candidates(
        original,
        task_id=TASK_ID,
        scans=(scan(candidate(bbox=(0, 0, 10, 10))),),
    )

    assert merged.findings[0] is rejected
    assert len(merged.findings) == 2
    assert merged.findings[1].review_status is ReviewStatus.NEEDS_REVIEW
    assert merged.inspection_result is InspectionResult.INCONCLUSIVE


def test_rescan_after_rejection_gets_a_distinct_stable_finding_id() -> None:
    evidence = scan(candidate())
    first = merge_open_candidates(decision(), task_id=TASK_ID, scans=(evidence,))
    rejected = Finding.model_validate(
        first.findings[0].model_dump()
        | {"review_status": ReviewStatus.REJECTED, "confirmed_by": None}
    )
    after_rejection = decision(rejected)

    rescanned = merge_open_candidates(after_rejection, task_id=TASK_ID, scans=(evidence,))
    independently_repeated = merge_open_candidates(
        after_rejection, task_id=TASK_ID, scans=(evidence,)
    )

    assert len(rescanned.findings) == 2
    assert rescanned.findings[1].finding_id != rejected.finding_id
    assert rescanned.findings[1].finding_id == independently_repeated.findings[1].finding_id


@pytest.mark.parametrize(
    "existing_changes,scan_changes,new_candidate",
    [
        ({}, {"asset_id": OTHER_ASSET_ID}, candidate(bbox=(0, 0, 10, 10))),
        ({}, {"asset_version": 2}, candidate(bbox=(0, 0, 10, 10))),
        (
            {"frame_index": 7, "timestamp_seconds": 1.4},
            {"frame_index": 8, "timestamp_seconds": 1.6},
            candidate(bbox=(0, 0, 10, 10)),
        ),
        ({}, {}, candidate(DetectionLabel.PL, bbox=(0, 0, 10, 10))),
        ({}, {}, candidate(bbox=(6, 0, 16, 10))),
    ],
)
def test_dedupe_does_not_cross_asset_version_frame_class_or_iou_boundary(
    existing_changes, scan_changes, new_candidate
) -> None:
    old = finding(**existing_changes)
    original = decision(old, result=InspectionResult.DEFECTS_FOUND)

    merged = merge_open_candidates(
        original,
        task_id=TASK_ID,
        scans=(scan(new_candidate, **scan_changes),),
    )

    assert len(merged.findings) == 2
    assert merged.findings[0] is old


def test_existing_high_confidence_confirmation_and_defect_result_are_never_modified() -> None:
    confirmed = finding(
        finding_id=19,
        label=DetectionLabel.BLOCK,
        score=1.0,
        bbox=(1, 1, 30, 30),
        source=FindingSource.HUMAN,
    )
    original = decision(
        confirmed,
        result=InspectionResult.DEFECTS_FOUND,
        reasons=("existing_reason",),
    )
    before = confirmed.model_dump(mode="json")

    merged = merge_open_candidates(
        original,
        task_id=TASK_ID,
        scans=(scan(candidate(DetectionLabel.FS, name="corrosion")),),
    )

    assert merged.findings[0] is confirmed
    assert merged.findings[0].model_dump(mode="json") == before
    assert merged.inspection_result is InspectionResult.DEFECTS_FOUND
    assert original.findings[0].model_dump(mode="json") == before


def test_incomplete_scan_blocks_negative_audit_and_normal_but_preserves_other_coverage() -> None:
    original = decision()

    merged = merge_open_candidates(original, task_id=TASK_ID, scans=(), incomplete=True)

    assert merged.findings == ()
    assert merged.coverage.sufficient_coverage is True
    assert merged.coverage.sufficient_quality is True
    assert merged.coverage.negative_audit_complete is False
    assert merged.inspection_result is InspectionResult.INCONCLUSIVE
    assert merged.needs_review is True
    assert "open_scan_incomplete" in merged.reasons


def test_incomplete_scan_preserves_confirmed_defect_result_and_finding() -> None:
    confirmed = finding()
    original = decision(confirmed, result=InspectionResult.DEFECTS_FOUND)

    merged = merge_open_candidates(original, task_id=TASK_ID, scans=(), incomplete=True)

    assert merged.findings == (confirmed,)
    assert merged.inspection_result is InspectionResult.DEFECTS_FOUND
    assert merged.needs_review is True
    assert merged.coverage.negative_audit_complete is False


def test_merge_has_stable_ids_and_is_idempotent() -> None:
    original = decision()
    evidence = scan(candidate(DetectionLabel.FS, name="surface corrosion"))

    first = merge_open_candidates(original, task_id=TASK_ID, scans=(evidence,))
    independently_repeated = merge_open_candidates(original, task_id=TASK_ID, scans=(evidence,))
    merged_twice = merge_open_candidates(first, task_id=TASK_ID, scans=(evidence,))

    assert first.findings[0].finding_id == independently_repeated.findings[0].finding_id
    assert merged_twice == first
