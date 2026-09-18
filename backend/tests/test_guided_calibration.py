from pipe_inspector.evaluation.guided_calibration import calibrate_class_thresholds


def box(label, x1, y1, x2, y2, score=1.0):
    return {"label": label, "bbox_xyxy": [x1, y1, x2, y2], "score": score}


def test_class_threshold_prefers_highest_recall_at_precision_target():
    records = [
        {
            "dataset": "sewer2403",
            "ground_truth": [box("PL", 0, 0, 10, 10)],
            "predictions": [box("PL", 0, 0, 10, 10, 0.62)],
        },
        {
            "dataset": "sewer2403",
            "ground_truth": [],
            "predictions": [box("PL", 20, 20, 30, 30, 0.40)],
        },
    ]

    result = calibrate_class_thresholds(
        records,
        labels=("PL",),
        thresholds=(0.4, 0.6, 0.7),
        target_precision=0.75,
    )

    assert result["PL"]["threshold"] == 0.6
    assert result["PL"]["selection"] == "precision_target"
    assert result["PL"]["counts"] == {"tp": 1, "fp": 0, "fn": 0}


def test_sedd_only_scores_pl_and_block_for_other_labels():
    records = [
        {
            "dataset": "sedd",
            "ground_truth": [box("PL", 0, 0, 10, 10)],
            "predictions": [
                box("PL", 0, 0, 10, 10, 0.8),
                box("FS", 20, 20, 30, 30, 0.9),
            ],
        }
    ]

    result = calibrate_class_thresholds(
        records,
        labels=("PL", "FS"),
        thresholds=(0.0, 0.8),
        target_precision=0.75,
    )

    assert result["PL"]["counts"]["tp"] == 1
    assert result["FS"]["status"] == "unscored_source"


def test_no_precision_target_uses_f1_fallback_and_exposes_counts():
    records = [
        {
            "dataset": "sewer2403",
            "ground_truth": [box("BX", 0, 0, 10, 10)],
            "predictions": [box("BX", 20, 20, 30, 30, 0.9)],
        }
    ]

    result = calibrate_class_thresholds(
        records,
        labels=("BX",),
        thresholds=(0.0, 0.9),
        target_precision=0.75,
    )

    assert result["BX"]["selection"] == "max_f1_fallback"
    assert set(result["BX"]["counts"]) == {"tp", "fp", "fn"}
