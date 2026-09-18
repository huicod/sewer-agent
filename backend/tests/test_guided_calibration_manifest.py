from pipe_inspector.evaluation.guided_calibration import (
    build_calibration_manifest,
    summarize_human_calibration,
)


def candidate(sample_id, group_id, labels):
    return {
        "sample_id": sample_id,
        "group_id": group_id,
        "dataset": "sewer2403",
        "labels": labels,
        "image": f"images/val/{sample_id}.jpg",
        "width": 640,
        "height": 480,
    }


def test_manifest_is_deterministic_group_disjoint_and_stratified():
    candidates = [
        candidate("empty", "g-empty", []),
        candidate("pl-1", "g-pl-1", ["PL"]),
        candidate("pl-duplicate", "g-pl-1", ["PL"]),
        candidate("bx-1", "g-bx-1", ["BX"]),
        candidate("fs-1", "g-fs-1", ["FS"]),
        candidate("block-1", "g-block-1", ["BLOCK"]),
        candidate("aj-1", "g-aj-1", ["AJ"]),
    ]

    first = build_calibration_manifest(candidates, max_samples=6)
    second = build_calibration_manifest(candidates, max_samples=6)

    assert first == second
    assert len(first) == 6
    assert len({item["group_id"] for item in first}) == len(first)
    assert {label for item in first for label in item["labels"]} == {
        "PL",
        "BX",
        "FS",
        "BLOCK",
        "AJ",
    }
    assert all(item["human_review"]["status"] == "pending" for item in first)


def test_pending_human_labels_block_accuracy_claims():
    records = [
        {"sample_id": "a", "human_review": {"status": "pending"}},
        {
            "sample_id": "b",
            "human_review": {
                "status": "complete",
                "reviewer": "tester",
                "expected_candidate_count": 0,
                "expected_addition_count": 0,
                "candidate_decisions": [],
                "addition_decisions": [],
            },
        },
    ]

    result = summarize_human_calibration(records)

    assert result == {"status": "pending", "pending_images": 1, "can_claim_accuracy": False}


def test_completed_human_labels_report_addition_precision():
    records = [
        {
            "sample_id": "a",
            "human_review": {
                "status": "complete",
                "reviewer": "tester",
                "expected_candidate_count": 0,
                "expected_addition_count": 2,
                "candidate_decisions": [],
                "addition_decisions": [
                    {"index": 0, "correct": True},
                    {"index": 1, "correct": False},
                ],
            },
        }
    ]

    result = summarize_human_calibration(records)

    assert result["status"] == "complete"
    assert result["can_claim_accuracy"] is True
    assert result["addition_precision"] == 0.5


def test_completed_human_labels_report_candidate_precision_by_vlm_action():
    records = [
        {
            "sample_id": "a",
            "human_review": {
                "status": "complete",
                "reviewer": "tester",
                "expected_candidate_count": 3,
                "expected_addition_count": 1,
                "candidate_decisions": [
                    {"verdict": "accept", "correct": True},
                    {"verdict": "reject", "correct": False},
                    {"verdict": "reject", "correct": True},
                ],
                "addition_decisions": [{"correct": True}],
            },
        }
    ]

    result = summarize_human_calibration(records)

    assert result["candidate_precision"] == 2 / 3
    assert result["candidate_precision_by_verdict"] == {
        "accept": 1.0,
        "reject": 0.5,
    }
    assert result["precision_by_action"]["addition"] == 1.0
