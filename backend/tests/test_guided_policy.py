from uuid import UUID

import pytest
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.findings import FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary
from pipe_inspector.contracts.tasks import InspectionResult

TASK_ID = UUID("00000000-0000-4000-8000-000000000001")
ASSET = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000002"))


def _observation(*detections: Detection, adequate: bool = True) -> ImageObservation:
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="cached",
            model_version="fixed",
            label_space_version="five",
            width=100,
            height=80,
            detections=detections,
        ),
        quality=QualitySummary(
            mean_luminance=100,
            contrast=30,
            sharpness=80,
            adequate=adequate,
            reasons=() if adequate else ("too_dark",),
        ),
    )


def _detection(
    label: DetectionLabel = DetectionLabel.PL,
    score: float = 0.7,
    box: tuple[float, float, float, float] = (10, 10, 30, 30),
) -> Detection:
    return Detection(label=label, native_label=label.value, score=score, bbox_xyxy=box)


def _candidate(
    index: int = 0,
    verdict: str = "accept",
    *,
    strength: str = "clear",
    proposed_label: str | None = None,
) -> dict:
    return {
        "index": index,
        "verdict": verdict,
        "proposed_label": proposed_label,
        "evidence_strength": strength,
        "original_presence": "present" if verdict == "accept" else "uncertain",
        "reason": "可见证据",
    }


def _review(
    candidates: list[dict] | None = None,
    additions: list[dict] | None = None,
    *,
    assessable: bool = True,
    audited: bool = True,
) -> GuidedReview:
    return GuidedReview.model_validate(
        {
            "image_assessable": assessable,
            "full_image_audited": audited,
            "candidates": candidates or [],
            "additional_anomalies": additions or [],
            "summary": "完整复核",
        }
    )


@pytest.mark.parametrize("verdict", ["reject", "revise"])
def test_high_score_conflict_keeps_literal_detector_evidence_pending(verdict: str) -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    detection = _detection(score=0.85)
    proposed = "FS" if verdict == "revise" else None
    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(detection),
        _review([_candidate(verdict=verdict, proposed_label=proposed)]),
    )

    finding = decision.findings[0]
    assert finding.canonical_label is DetectionLabel.PL
    assert finding.score == 0.85
    assert finding.evidence[0].region.bbox_xyxy == detection.bbox_xyxy
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE


def test_low_score_clear_rejection_is_retained_as_pending_audit_evidence() -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(_detection(score=0.8499)),
        _review([_candidate(verdict="reject")]),
    )

    finding = decision.findings[0]
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert finding.canonical_label is DetectionLabel.PL
    assert finding.score == 0.8499
    assert finding.evidence[0].region.bbox_xyxy == (10, 10, 30, 30)
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True


def test_clear_revision_keeps_original_label_pending_and_preserves_score_and_box() -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(_detection(score=0.7)),
        _review([_candidate(verdict="revise", proposed_label="FS")]),
    )

    finding = decision.findings[0]
    assert finding.canonical_label is DetectionLabel.PL
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert finding.confirmed_by is None
    assert finding.source is FindingSource.DETECTOR
    assert finding.score == 0.7
    assert finding.evidence[0].region.bbox_xyxy == (10, 10, 30, 30)
    assert "guided_advice_requires_review" in decision.reasons


def test_clear_addition_is_vlm_advice_pending_without_synthetic_confidence() -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    additions = [
        {
            "label": "FS",
            "bbox_xyxy": [40, 40, 60, 60],
            "evidence_strength": "clear",
            "reason": "明确腐蚀",
        },
        {
            "label": "OTHER",
            "name": "树根",
            "bbox_xyxy": [70, 40, 90, 60],
            "evidence_strength": "clear",
            "reason": "五类外异常",
        },
    ]
    decision = decide_image_guided(TASK_ID, ASSET, _observation(), _review(additions=additions))

    added, other = decision.findings
    assert (added.source, added.score, added.review_status, added.confirmed_by) == (
        FindingSource.VLM,
        None,
        ReviewStatus.NEEDS_REVIEW,
        None,
    )
    assert other.canonical_label == "OTHER"
    assert other.open_label == "树根"
    assert other.review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True


@pytest.mark.parametrize(
    "review",
    [
        _review([_candidate(strength="uncertain")]),
        _review(
            additions=[
                {
                    "label": "FS",
                    "bbox_xyxy": [40, 40, 60, 60],
                    "evidence_strength": "uncertain",
                    "reason": "证据不确定",
                }
            ]
        ),
    ],
)
def test_uncertain_candidate_or_addition_remains_advisory(review: GuidedReview) -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    observation = _observation(_detection()) if review.candidates else _observation()
    decision = decide_image_guided(TASK_ID, ASSET, observation, review)

    assert all(item.review_status is ReviewStatus.NEEDS_REVIEW for item in decision.findings)
    assert decision.needs_review is True
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE


def test_review_failure_protects_every_raw_box_and_ignores_supplied_review() -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    detections = (
        _detection(score=0.4),
        _detection(DetectionLabel.BLOCK, 0.9, (50, 10, 80, 30)),
    )
    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(*detections),
        _review([_candidate(0, "reject"), _candidate(1, "accept")]),
        review_error="timeout",
    )

    assert [item.canonical_label for item in decision.findings] == ["PL", "BLOCK"]
    assert [item.score for item in decision.findings] == [0.4, 0.9]
    assert all(item.review_status is ReviewStatus.NEEDS_REVIEW for item in decision.findings)
    assert "review_failed" in decision.reasons


@pytest.mark.parametrize(
    ("adequate", "assessable", "audited", "reason", "expected_status"),
    [
        (False, True, True, "quality_inadequate", ReviewStatus.NEEDS_REVIEW),
        (True, False, True, "image_not_assessable", ReviewStatus.NEEDS_REVIEW),
        (True, True, False, "full_image_audit_incomplete", ReviewStatus.CONFIRMED),
    ],
)
def test_quality_or_whole_image_gates_block_normal_and_rule_confirmation(
    adequate: bool,
    assessable: bool,
    audited: bool,
    reason: str,
    expected_status: ReviewStatus,
) -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(_detection(), adequate=adequate),
        _review([_candidate()], assessable=assessable, audited=audited),
    )

    assert decision.findings[0].review_status is expected_status
    assert decision.inspection_result is not InspectionResult.NORMAL
    assert decision.needs_review is True
    assert reason in decision.reasons


def test_semantically_invalid_received_review_fails_closed() -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    invalid = _review([_candidate(index=1)])
    decision = decide_image_guided(TASK_ID, ASSET, _observation(_detection()), invalid)

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert "review_invalid" in decision.reasons


@pytest.mark.parametrize(
    ("verdict", "original_presence"),
    [("accept", "absent"), ("reject", "present"), ("revise", "present")],
)
def test_original_presence_contradiction_fails_closed(
    verdict: str, original_presence: str
) -> None:
    from pipe_inspector.agent.guided_policy import decide_image_guided

    payload = _candidate(verdict=verdict, proposed_label="FS" if verdict == "revise" else None)
    payload["original_presence"] = original_presence
    decision = decide_image_guided(
        TASK_ID,
        ASSET,
        _observation(_detection()),
        _review([payload]),
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert "review_invalid" in decision.reasons


def test_unconfirmed_addition_is_explicitly_gated() -> None:
    from pipe_inspector.agent.guided_fusion import fuse_guided

    review = _review(
        additions=[
            {
                "label": "FS",
                "bbox_xyxy": [40, 40, 60, 60],
                "evidence_strength": "clear",
                "reason": "可见腐蚀",
            }
        ]
    )
    fused = fuse_guided(_observation(), review)

    assert fused["changes"][0]["action"] == "add_pending"
    assert "二次确认不足" in fused["changes"][0]["gate"]
