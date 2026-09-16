from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("scale_diagnostic.py")


def load_module():
    spec = importlib.util.spec_from_file_location("test_scale_diagnostic_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_overlapping_tile_windows_cover_edges_with_literal_coordinates() -> None:
    diagnostic = load_module()

    assert diagnostic.tile_windows(1000, 700, tile_size=400, overlap=100) == [
        (0, 0, 400, 400),
        (300, 0, 700, 400),
        (600, 0, 1000, 400),
        (0, 300, 400, 700),
        (300, 300, 700, 700),
        (600, 300, 1000, 700),
    ]
    assert diagnostic.tile_windows(320, 200, tile_size=400, overlap=100) == [
        (0, 0, 320, 200)
    ]


def test_tile_mapping_offsets_clips_and_drops_empty_boxes() -> None:
    diagnostic = load_module()

    mapped = diagnostic.map_tile_predictions(
        [
            {"label": "PL", "score": 0.8, "bbox_xyxy": [-5.0, 10.0, 430.0, 500.0]},
            {"label": "FS", "score": 0.7, "bbox_xyxy": [-20.0, 5.0, -1.0, 20.0]},
        ],
        tile=(600, 300, 1000, 700),
        image_width=1000,
        image_height=700,
    )

    assert mapped == [
        {"label": "PL", "score": 0.8, "bbox_xyxy": [600.0, 310.0, 1000.0, 700.0]}
    ]


def test_class_aware_nms_suppresses_only_same_class_duplicates() -> None:
    diagnostic = load_module()
    predictions = [
        {"label": "PL", "score": 0.9, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "PL", "score": 0.8, "bbox_xyxy": [1.0, 1.0, 11.0, 11.0]},
        {"label": "BLOCK", "score": 0.7, "bbox_xyxy": [1.0, 1.0, 11.0, 11.0]},
        {"label": "PL", "score": 0.6, "bbox_xyxy": [20.0, 20.0, 30.0, 30.0]},
    ]

    assert diagnostic.class_aware_nms(predictions, iou_threshold=0.5) == [
        predictions[0],
        predictions[2],
        predictions[3],
    ]


def test_matching_is_class_aware_score_ordered_and_one_to_one() -> None:
    diagnostic = load_module()
    targets = [
        {"label": "PL", "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "PL", "bbox_xyxy": [20.0, 20.0, 30.0, 30.0]},
    ]
    predictions = [
        {"label": "PL", "score": 0.7, "bbox_xyxy": [20.0, 20.0, 30.0, 30.0]},
        {"label": "PL", "score": 0.9, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "PL", "score": 0.8, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "BLOCK", "score": 0.95, "bbox_xyxy": [20.0, 20.0, 30.0, 30.0]},
    ]

    assert diagnostic.match_detections(targets, predictions, iou_threshold=0.5) == [(1, 0), (0, 1)]
    assert diagnostic.score_records(targets, predictions, 100, 100)["overall"] == {
        "tp": 2,
        "fp": 2,
        "fn": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
    }


def test_area_metrics_use_target_area_for_matches_and_prediction_area_for_false_positives() -> None:
    diagnostic = load_module()
    targets = [
        {"label": "PL", "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "BLOCK", "bbox_xyxy": [0.0, 0.0, 80.0, 80.0]},
    ]
    predictions = [
        {"label": "PL", "score": 0.9, "bbox_xyxy": [0.0, 0.0, 10.0, 10.0]},
        {"label": "FS", "score": 0.8, "bbox_xyxy": [50.0, 50.0, 60.0, 60.0]},
    ]

    result = diagnostic.score_records(targets, predictions, 100, 100)
    assert result["area_bins"]["1-5%"] == {
        "tp": 1,
        "fp": 1,
        "fn": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
    }
    assert result["area_bins"][">=20%"] == {
        "tp": 0,
        "fp": 0,
        "fn": 1,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }


def test_crossing_families_reports_only_groups_present_in_multiple_splits() -> None:
    diagnostic = load_module()
    rows = [
        {"image": "a_frame1.jpg", "group_key": "video:a", "split": "train"},
        {"image": "a_frame2.jpg", "group_key": "video:a", "split": "test"},
        {"image": "b_frame1.jpg", "group_key": "video:b", "split": "val"},
        {"image": "b_frame2.jpg", "group_key": "video:b", "split": "val"},
    ]

    assert diagnostic.crossing_families(rows) == [
        {
            "group_key": "video:a",
            "images": ["a_frame1.jpg", "a_frame2.jpg"],
            "splits": ["test", "train"],
        }
    ]


def test_gallery_choice_skips_duplicate_ranked_cases() -> None:
    diagnostic = load_module()
    a = {"sample_id": "a"}
    b = {"sample_id": "b"}
    c = {"sample_id": "c"}

    assert diagnostic.distinct_gallery_choices(
        [("largest TP gain", [a, b]), ("largest FP increase", [a, c])],
        [a, b, c],
        limit=3,
    ) == [
        (a, "largest TP gain"),
        (c, "largest FP increase"),
        (b, "fixed-ID context case"),
    ]
