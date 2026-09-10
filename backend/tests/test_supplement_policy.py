from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.agent.image_policy import decide_image
from pipe_inspector.agent.supplement_policy import decide_supplement
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview
from pipe_inspector.contracts.tasks import TaskSnapshot


def observation(detected=True):
    return ImageObservation.model_validate(
        {
            "detection": {
                "detector_id": "fixture",
                "model_version": "1",
                "label_space_version": "five",
                "width": 32,
                "height": 32,
                "detections": [
                    {"label": "PL", "native_label": "PL", "score": 0.4, "bbox_xyxy": [1, 1, 20, 20]}
                ]
                if detected
                else [],
            },
            "quality": {"mean_luminance": 120, "contrast": 40, "sharpness": 500, "adequate": True},
        }
    )


def review(verdict=None, additional=()):
    return ImageReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "candidates": []
            if verdict is None
            else [{"index": 0, "verdict": verdict, "reason": "visual evidence"}],
            "additional_anomalies": additional,
            "summary": "fixture review",
        }
    )


def previous(asset, observed, reviewed):
    task_id = uuid4()
    decision = decide_image(task_id, asset, observed, reviewed)
    return TaskSnapshot(
        task_id=task_id,
        updated_at=datetime.now(timezone.utc),
        runtime_status="partial" if decision.needs_review else "completed",
        inspection_result=decision.inspection_result,
        findings=decision.findings,
        coverage=decision.coverage,
    )


def test_prior_open_candidate_cannot_disappear_into_normal():
    asset = AssetRef(asset_id=uuid4())
    observed = observation(False)
    old_review = review(
        additional=[{"label": "OTHER", "name": "疑似异物", "reason": "visible anomaly"}]
    )
    old = previous(asset, observed, old_review)
    saved = old.model_dump(mode="json")
    child = uuid4()
    result = decide_supplement(
        child, asset, observed, review(), previous=old, previous_review=old_review
    )
    assert result.inspection_result == "inconclusive"
    assert result.needs_review
    assert len(result.findings) == 1
    assert result.findings[0].canonical_label == "OTHER"
    assert result.findings[0].review_status == "needs_review"
    assert result.findings[0].task_id == child
    assert old.model_dump(mode="json") == saved


@pytest.mark.parametrize("earlier,later", [("accept", "reject"), ("reject", "accept")])
def test_substantive_review_disagreement_remains_pending(earlier, later):
    asset = AssetRef(asset_id=uuid4())
    observed = observation()
    old_review = review(earlier)
    result = decide_supplement(
        uuid4(),
        asset,
        observed,
        review(later),
        previous=previous(asset, observed, old_review),
        previous_review=old_review,
    )
    assert result.inspection_result == "inconclusive"
    assert result.findings[0].review_status == "needs_review"
    assert "prior_review_conflict" in result.reasons


def test_abstain_can_be_resolved_by_consistent_re_review():
    asset = AssetRef(asset_id=uuid4())
    observed = observation()
    old_review = review("abstain")
    result = decide_supplement(
        uuid4(),
        asset,
        observed,
        review("accept"),
        previous=previous(asset, observed, old_review),
        previous_review=old_review,
    )
    assert result.inspection_result == "defects_found"
    assert result.findings[0].review_status == "confirmed"
    assert not result.needs_review


@pytest.mark.parametrize("error,want", [(None, "normal"), ("timeout", "inconclusive")])
def test_missing_initial_review_can_be_resolved_only_after_success(error, want):
    asset = AssetRef(asset_id=uuid4())
    observed = observation(False)
    result = decide_supplement(
        uuid4(),
        asset,
        observed,
        review(),
        previous=previous(asset, observed, None),
        previous_review=None,
        review_error=error,
    )
    assert result.inspection_result == want


def test_repeated_additional_suggestion_is_not_duplicated():
    asset = AssetRef(asset_id=uuid4())
    observed = observation(False)
    advice = review(
        additional=[
            {
                "label": "FS",
                "name": "腐蚀",
                "bbox_xyxy": [1, 1, 20, 20],
                "reason": "visible anomaly",
            }
        ]
    )
    result = decide_supplement(
        uuid4(),
        asset,
        observed,
        advice,
        previous=previous(asset, observed, advice),
        previous_review=advice,
    )
    assert len(result.findings) == 1
    assert result.findings[0].review_status == "needs_review"
