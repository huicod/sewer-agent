from pipe_inspector.evaluation.guided_replay import replay_state


def _state() -> dict:
    return {
        "records": [
            {
                "sample_id": "fixture",
                "dataset": "sewer2403",
                "observation": {
                    "detection": {
                        "detector_id": "fixture",
                        "model_version": "v1",
                        "label_space_version": "five",
                        "width": 100,
                        "height": 80,
                        "detections": [
                            {
                                "label": "PL",
                                "native_label": "PL",
                                "score": 0.7,
                                "bbox_xyxy": [10, 10, 50, 50],
                            }
                        ],
                    },
                    "quality": {
                        "mean_luminance": 100,
                        "contrast": 30,
                        "sharpness": 80,
                        "adequate": True,
                        "reasons": [],
                    },
                },
                "review": {
                    "image_assessable": True,
                    "full_image_audited": True,
                    "candidates": [
                        {
                            "index": 0,
                            "verdict": "revise",
                            "proposed_label": "FS",
                            "evidence_strength": "clear",
                            "reason": "fixture advice",
                        }
                    ],
                    "additional_anomalies": [
                        {
                            "label": "BLOCK",
                            "bbox_xyxy": [60, 20, 90, 60],
                            "evidence_strength": "clear",
                            "reason": "fixture addition",
                        }
                    ],
                    "summary": "fixture review",
                },
                "review_error": None,
                "ground_truth": [
                    {"label": "PL", "bbox_xyxy": [10, 10, 50, 50]}
                ],
            }
        ]
    }


def test_replay_switches_policy_without_provider_access():
    conservative = replay_state(_state())
    legacy = replay_state(_state(), policy="guided-confidence-v2")

    assert conservative["provider_attempts"] == 0
    assert conservative["summary"]["micro"]["tp"] == 1
    assert conservative["summary"]["micro"]["fp"] == 0
    assert conservative["records"][0]["changes"][0]["action"] == "retain_high_confidence"
    assert conservative["records"][0]["changes"][1]["action"] == "add_pending"
    assert legacy["records"][0]["changes"][0]["action"] == "revise"
    assert legacy["records"][0]["changes"][1]["action"] == "add"
