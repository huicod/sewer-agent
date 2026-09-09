import pytest
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageReview,
    QualitySummary,
)
from pydantic import ValidationError


def test_review_contracts_roundtrip_other_and_candidate_advice() -> None:
    review = ImageReview(
        image_assessable=True,
        full_image_audited=True,
        candidates=(
            CandidateReview(
                index=0,
                verdict="revise",
                proposed_label="BX",
                reason="边缘形态更接近变形",
            ),
        ),
        additional_anomalies=(
            AdditionalAnomaly(
                label="OTHER",
                name="疑似异物",
                bbox_xyxy=(1, 2, 8, 9),
                reason="不属于当前五类",
            ),
        ),
        summary="需要人工确认两个候选。",
    )

    restored = ImageReview.model_validate_json(review.model_dump_json())

    assert restored.candidates[0].proposed_label == "BX"
    assert restored.additional_anomalies[0].label == "OTHER"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CandidateReview, {"index": -1, "verdict": "accept", "reason": "valid"}),
        (CandidateReview, {"index": 0, "verdict": "guess", "reason": "valid"}),
        (CandidateReview, {"index": 0, "verdict": "accept", "reason": " "}),
        (
            AdditionalAnomaly,
            {"label": "OTHER", "bbox_xyxy": (0, 0, float("nan"), 2), "reason": "valid"},
        ),
        (QualitySummary, {"mean_luminance": float("inf"), "contrast": 1, "sharpness": 1}),
    ],
)
def test_inspection_contracts_reject_invalid_model_output(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)
