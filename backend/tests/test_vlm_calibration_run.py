from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.inspection import ImageObservation


def candidate(label="PL", score=0.6, box=(10, 10, 30, 30)):
    return {"label": label, "score": score, "bbox_xyxy": list(box)}


def test_reconstruct_observation_uses_saved_detector_boxes_and_dimensions():
    from pipe_inspector.evaluation.vlm_calibration_run import reconstruct_observation

    observation = reconstruct_observation(
        width=640,
        height=480,
        candidates=[candidate("BX", 0.42, (1, 2, 40, 50))],
    )

    assert isinstance(observation, ImageObservation)
    assert observation.detection.detector_id == "ultralytics-yolo26m-validation-cache"
    assert observation.detection.model_version == "merged_five_yolo26m_960_5090/best"
    assert observation.detection.detections[0].label is DetectionLabel.BX
    assert observation.detection.detections[0].score == 0.42
    assert observation.detection.width == 640
    assert observation.detection.height == 480


def test_reconstruct_observation_clips_export_rounding_at_image_bounds():
    from pipe_inspector.evaluation.vlm_calibration_run import reconstruct_observation

    observation = reconstruct_observation(
        width=100,
        height=80,
        candidates=[candidate("PL", 0.4, (-0.001, -0.002, 100.001, 80.002))],
    )

    assert observation.detection.detections[0].bbox_xyxy == (0.0, 0.0, 100.0, 80.0)


def test_filter_candidates_uses_production_confidence_floor():
    from pipe_inspector.evaluation.vlm_calibration_run import filter_candidates

    boxes = [candidate(score=0.249), candidate(score=0.25)]
    assert [item["score"] for item in filter_candidates(boxes)] == [0.25]


def test_independent_confirmation_accepts_only_clear_present_candidate():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.vlm_calibration_run import independent_confirmation_accepts

    accepted = GuidedReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "summary": "fixture",
            "candidates": [
                {
                    "index": 0,
                    "verdict": "accept",
                    "proposed_label": "PL",
                    "reason": "可见连续裂缝",
                    "evidence_strength": "clear",
                    "original_presence": "present",
                }
            ],
            "additional_anomalies": [],
        }
    )
    assert independent_confirmation_accepts(accepted, "PL") is True

    rejected = accepted.model_copy(
        update={
            "candidates": [
                accepted.candidates[0].model_copy(
                    update={"verdict": "abstain", "proposed_label": None}
                )
            ]
        }
    )
    assert independent_confirmation_accepts(rejected, "PL") is False


def test_experimental_additions_require_confirmation_and_non_overlap():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.vlm_calibration_run import (
        experimental_addition_boxes,
        reconstruct_observation,
    )

    observation = reconstruct_observation(640, 480, [candidate()])
    review = GuidedReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "summary": "fixture",
            "candidates": [
                {
                    "index": 0,
                    "verdict": "accept",
                    "proposed_label": None,
                    "reason": "fixture",
                    "evidence_strength": "clear",
                    "original_presence": "present",
                }
            ],
            "additional_anomalies": [
                {
                    "label": "FS",
                    "bbox_xyxy": [100, 100, 150, 150],
                    "reason": "confirmed",
                    "evidence_strength": "clear",
                    "confirmation_status": "confirmed",
                },
                {
                    "label": "BX",
                    "bbox_xyxy": [11, 11, 29, 29],
                    "reason": "overlap",
                    "evidence_strength": "clear",
                    "confirmation_status": "confirmed",
                },
                {
                    "label": "PL",
                    "bbox_xyxy": [200, 200, 230, 230],
                    "reason": "not confirmed",
                    "evidence_strength": "clear",
                    "confirmation_status": "uncertain",
                },
            ],
        }
    )

    boxes = experimental_addition_boxes(observation, review)

    assert [(item.label.value, item.bbox_xyxy) for item in boxes] == [
        ("FS", (100, 100, 150, 150))
    ]


def test_experimental_additions_never_accept_other_label():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.vlm_calibration_run import (
        experimental_addition_boxes,
        reconstruct_observation,
    )

    observation = reconstruct_observation(100, 100, [])
    review = GuidedReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "summary": "fixture",
            "candidates": [],
            "additional_anomalies": [
                {
                    "label": "OTHER",
                    "name": "fixture",
                    "bbox_xyxy": [10, 10, 20, 20],
                    "reason": "other",
                    "evidence_strength": "clear",
                    "confirmation_status": "confirmed",
                }
            ],
        }
    )

    assert experimental_addition_boxes(observation, review) == []
