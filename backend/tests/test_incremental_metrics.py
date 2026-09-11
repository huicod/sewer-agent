import csv

import pytest
from pipe_inspector.evaluation.incremental_metrics import (
    export_review_queue,
    summarize_incremental,
)


def _box(label: str, bbox: tuple[int, int, int, int], score: float = 0.9) -> dict:
    return {"label": label, "bbox_xyxy": list(bbox), "score": score}


def _records() -> list[dict]:
    return [
        {
            "sample_id": "image-1",
            "source_kind": "image",
            "ground_truth": [
                _box("PL", (0, 0, 10, 10), 1.0),
                _box("FS", (20, 20, 30, 30), 1.0),
            ],
            "primary": [
                _box("PL", (0, 0, 10, 10)),
                _box("PL", (20, 20, 30, 30)),
            ],
            "guided": [_box("FS", (20, 20, 30, 30))],
            "combined": [
                _box("FS", (20, 20, 30, 30)),
                _box("PL", (0, 0, 10, 10), 0.7),
            ],
            "open_added": [
                {
                    "label": "PL",
                    "name": "fine crack",
                    "score": 0.7,
                    "bbox_xyxy": [0, 0, 10, 10],
                },
                {
                    "label": "OTHER",
                    "name": "water line",
                    "score": 0.6,
                    "bbox_xyxy": [40, 40, 50, 50],
                },
            ],
            "open_findings": [
                dict(
                    finding_id="00000000-0000-4000-8000-000000000001",
                    canonical_label="PL",
                    evidence=[dict(region=dict(bbox_xyxy=[0, 0, 10, 10]))],
                ),
                dict(
                    finding_id="00000000-0000-4000-8000-000000000002",
                    canonical_label="OTHER",
                    evidence=[dict(region=dict(bbox_xyxy=[40, 40, 50, 50]))],
                ),
            ],
            "open_status": "succeeded",
            "open_duration_seconds": 1.25,
            "review_cached": True,
            "review_error": None,
            "review_abstained": True,
            "pending_change_count": 2,
            "trigger": "zero_detection_or_possible_miss",
            "ignored_future_key": {"allowed": True},
        },
        {
            "sample_id": "video-1",
            "source_kind": "video",
            "ground_truth": None,
            "primary": [_box("BLOCK", (5, 5, 15, 15))],
            "guided": [],
            "combined": [],
            "open_added": [],
            "open_status": "failed",
            "open_duration_seconds": 0.75,
            "review_cached": False,
            "review_error": "saved_failure",
            "review_abstained": False,
            "pending_change_count": 1,
            "trigger": "periodic_audit",
        },
    ]


def test_summary_reports_annotation_matching_and_target_recovery_by_stage() -> None:
    result = summarize_incremental(_records(), expected_count=2)

    image = result["cohorts"]["image"]
    primary = image["stages"]["primary"]
    guided = image["stages"]["guided"]
    combined = image["stages"]["combined"]

    assert primary["annotation_metrics"]["micro"] == {
        "tp": 1,
        "fp": 1,
        "fn": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }
    assert guided["annotation_metrics"]["micro"] == {
        "tp": 1,
        "fp": 0,
        "fn": 1,
        "precision": 1.0,
        "recall": 0.5,
        "f1": pytest.approx(2 / 3),
    }
    assert combined["annotation_metrics"]["micro"] == {
        "tp": 2,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert primary["annotation_unmatched_prediction_count"] == 1
    assert primary["annotation_unmatched_predictions_are_proven_false_positives"] is False
    assert combined["accuracy_claim"] == "tentative_candidate_coverage_only"
    assert image["per_image_deltas"] == [
        {
            "sample_id": "image-1",
            "primary_to_guided": {"recovered": 1, "lost": 1},
            "guided_to_combined": {"recovered": 1, "lost": 0},
        }
    ]


def test_unknown_video_ground_truth_is_not_treated_as_empty_or_scored() -> None:
    result = summarize_incremental(_records(), expected_count=2)

    video = result["cohorts"]["video"]
    assert video["record_count"] == 1
    assert video["annotated_count"] == 0
    assert video["unknown_ground_truth_count"] == 1
    assert video["per_image_deltas"] == []
    for stage in video["stages"].values():
        assert stage["annotation_metrics"] is None
        assert stage["annotation_unmatched_prediction_count"] is None
        assert stage["annotation_unmatched_predictions_are_proven_false_positives"] is False


def test_summary_reports_coverage_pending_other_failures_cache_and_unknown_cost() -> None:
    result = summarize_incremental(_records(), expected_count=3)

    assert result["status"] == "incomplete"
    assert result["complete"] is False
    assert result["coverage"] == {"expected_count": 3, "record_count": 2, "missing_count": 1}
    assert result["operations"] == {
        "open_detection": {
            "status_counts": {"skipped": 0, "succeeded": 1, "failed": 1},
            "pending_count": 2,
            "pending_other_count": 1,
            "duration_seconds": 2.0,
        },
        "review": {
            "cached_count": 1,
            "new_vlm_calls": 0,
            "failure_count": 1,
            "abstention_count": 1,
            "pending_change_count": 3,
        },
        "cost": {"currency": None, "amount": None},
    }


def test_summary_marks_optional_review_accounting_unavailable() -> None:
    records = _records()
    for record in records:
        record.pop("review_abstained")
        record.pop("pending_change_count")

    review = summarize_incremental(records, expected_count=2)["operations"]["review"]

    assert review["abstention_count"] is None
    assert review["pending_change_count"] is None


def test_summary_rejects_duplicate_ids_and_impossible_expected_count() -> None:
    records = _records()
    records[1]["sample_id"] = records[0]["sample_id"]

    with pytest.raises(ValueError, match="sample_id values must be unique"):
        summarize_incremental(records, expected_count=2)
    with pytest.raises(ValueError, match="expected_count"):
        summarize_incremental(_records(), expected_count=1)


def test_export_review_queue_writes_neutral_rows_with_csv_escaping(tmp_path) -> None:
    records = _records()
    records[0]["open_added"][0]["name"] = 'crack, "wide"\nsecond line'
    path = export_review_queue(records, tmp_path / "nested" / "review.csv")

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert path == tmp_path / "nested" / "review.csv"
    assert len(rows) == 2
    assert rows[0] == {
        "sample_id": "image-1",
        "finding_id": "00000000-0000-4000-8000-000000000001",
        "group_id": "",
        "image_path": "",
        "frame_index": "",
        "timestamp_seconds": "",
        "gallery_link": "gallery/sample-0001/index.html",
        "ordinal": "1",
        "source_kind": "image",
        "label": "PL",
        "name": 'crack, "wide"\nsecond line',
        "score": "0.7",
        "bbox_x1": "0.0",
        "bbox_y1": "0.0",
        "bbox_x2": "10.0",
        "bbox_y2": "10.0",
        "decision": "unreviewed",
        "corrected_label": "",
        "comment": "",
    }
    assert rows[1]["label"] == "OTHER"
    assert rows[1]["decision"] == "unreviewed"


def test_export_review_queue_preserves_existing_human_edits(tmp_path) -> None:
    path = export_review_queue(_records(), tmp_path / "review.csv")
    edited = path.read_text(encoding="utf-8").replace("unreviewed", "accepted", 1)
    path.write_text(edited, encoding="utf-8")

    changed_records = _records()
    changed_records[0]["open_added"] = []
    returned = export_review_queue(changed_records, path)

    assert returned == path
    assert path.read_text(encoding="utf-8") == edited


def test_review_queue_links_stable_finding_and_source_and_rejects_misalignment(tmp_path):
    records = _records()
    samples = [
        dict(
            sample_id="image-1",
            group_id="source-one",
            image_path="/example/a.png",
            frame_index=None,
            timestamp_seconds=None,
        ),
        dict(sample_id="video-1"),
    ]
    path = export_review_queue(records, tmp_path / "linked.csv", samples=samples)
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["image_path"] == "/example/a.png"
    assert rows[0]["group_id"] == "source-one"
    assert rows[0]["finding_id"].endswith("000000000001")
    records[0]["open_findings"][0]["canonical_label"] = "FS"
    with pytest.raises(ValueError, match="alignment"):
        export_review_queue(records, tmp_path / "invalid.csv", samples=samples)
