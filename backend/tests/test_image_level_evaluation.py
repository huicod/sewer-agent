"""Cached scoring must change the metric, never the predictions or denominator."""

import copy
import json

import pytest


def box(label, x=0):
    return {"label": label, "bbox_xyxy": [x, 0, x + 10, 10], "score": 0.5}


def record(sample_id="sample", dataset="sewer2403", gt=(), raw=(), additions=()):
    return {
        "sample_id": sample_id,
        "dataset": dataset,
        "image": f"images/val/{sample_id}.jpg",
        "ground_truth": list(gt),
        "observation": {"detection": {"detections": list(raw)}},
        "conservative_predictions": list(raw),
        "predictions": list(raw) + list(additions),
        "experimental_additions": list(additions),
        "review_error": None,
    }


def state(*records):
    return {
        "schema_version": "vlm-calibration-live-v1",
        "sample_ids": [item["sample_id"] for item in records],
        "records": list(records),
    }


def confirmation(*records):
    return {"schema_version": "vlm-calibration-addition-confirmation-v1", "records": list(records)}


def test_label_sets_ignore_location_and_duplicates_but_keep_box_reference():
    from pipe_inspector.evaluation.image_level import evaluate_cached

    source = state(record(gt=[box("FS"), box("FS", 20)], raw=[box("FS", 50), box("FS", 70)]))
    before = copy.deepcopy(source)
    result = evaluate_cached(source)
    image = result["variants"]["raw_yolo"]["image_level"]["mixed"]
    boxes = result["variants"]["raw_yolo"]["box_level"]["mixed"]
    assert image["micro"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1, "recall": 1, "f1": 1}
    assert image["exact_match_count"] == 1
    assert image["exact_match_rate"] == 1
    assert (boxes["micro"]["tp"], boxes["micro"]["fp"], boxes["micro"]["fn"]) == (0, 2, 2)
    assert source == before
    assert "confirmed_additions" not in result["variants"]


def test_sedd_unannotated_labels_are_not_false_positives_or_true_negatives():
    from pipe_inspector.evaluation.image_level import evaluate_cached

    source = state(
        record("s", "sedd", gt=[box("PL")], raw=[box("PL"), box("FS"), box("BX")]),
        record("n", gt=[], raw=[box("FS")]),
    )
    result = evaluate_cached(source)
    scores = result["variants"]["raw_yolo"]["image_level"]
    assert scores["sedd"]["micro"]["fp"] == 0
    assert scores["mixed"]["micro"]["fp"] == 1
    assert set(scores["sedd"]["per_class"]) == {"PL", "BLOCK"}
    assert scores["mixed"]["per_class"]["FS"]["evaluated_images"] == 1
    assert scores["mixed"]["per_class"]["PL"]["evaluated_images"] == 2
    assert result["records"][0]["variants"]["raw_yolo"]["ignored_prediction_labels"] == ["BX", "FS"]


def test_failed_review_and_empty_ground_truth_stay_in_denominator():
    from pipe_inspector.evaluation.image_level import evaluate_cached

    failed = record("miss", gt=[box("BX")])
    failed["review_error"] = "schema_validation"
    result = evaluate_cached(state(record("normal"), failed))
    scores = result["variants"]["raw_yolo"]["image_level"]["mixed"]
    assert result["review_failures"] == 1
    assert scores["images"] == 2
    assert scores["exact_match_rate"] == 0.5
    assert scores["micro"] == {"tp": 0, "fp": 0, "fn": 1, "precision": 0, "recall": 0, "f1": 0}


def test_saved_raw_detector_native_label_is_not_an_evaluation_class():
    from pipe_inspector.evaluation.image_level import evaluate_cached

    saved = record(gt=[box("PL")], raw=[box("PL")])
    saved["observation"]["detection"]["detections"] = [box("PL") | {"native_label": "PL"}]
    result = evaluate_cached(state(saved))
    assert result["variants"]["raw_yolo"]["image_level"]["mixed"]["micro"]["tp"] == 1


def test_confirmations_accept_only_matching_successes_and_count_missing():
    from pipe_inspector.evaluation.image_level import evaluate_cached

    additions = [box("FS"), box("BX"), box("PL")]
    source = state(record(gt=[box("FS"), box("BX"), box("PL")], additions=additions))
    responses = confirmation(
        {
            "key": "sample::0",
            "sample_id": "sample",
            "dataset": "sewer2403",
            "proposal": additions[0],
            "accepted": True,
            "review_error": None,
        },
        {
            "key": "sample::1",
            "sample_id": "sample",
            "dataset": "sewer2403",
            "proposal": additions[1],
            "accepted": True,
            "review_error": "schema_validation",
        },
    )
    result = evaluate_cached(source, responses)
    assert result["confirmation"] == {
        "provided": True,
        "proposals": 3,
        "reviewed": 2,
        "accepted": 1,
        "failed": 1,
        "missing": 1,
    }
    micro = result["variants"]["confirmed_additions"]["image_level"]["mixed"]["micro"]
    assert (micro["tp"], micro["fp"], micro["fn"]) == (1, 0, 2)
    assert result["variants"]["experimental"]["image_level"]["mixed"]["micro"]["tp"] == 3


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "source", "missing_prediction"])
def test_malformed_cohort_rejected_instead_of_silently_dropping_images(mutation):
    from pipe_inspector.evaluation.image_level import evaluate_cached

    source = state(record())
    if mutation == "missing":
        source["sample_ids"].append("absent")
    elif mutation == "duplicate":
        source["records"].append(source["records"][0])
    elif mutation == "source":
        source["records"][0]["dataset"] = "unknown"
    else:
        del source["records"][0]["conservative_predictions"]
    with pytest.raises(ValueError):
        evaluate_cached(source)


@pytest.mark.parametrize("mutation", ["key", "proposal", "duplicate", "accepted"])
def test_confirmation_cannot_be_reused_for_a_different_proposal(mutation):
    from pipe_inspector.evaluation.image_level import evaluate_cached

    source = state(record(additions=[box("FS")]))
    item = {
        "key": "sample::0",
        "sample_id": "sample",
        "dataset": "sewer2403",
        "proposal": box("FS"),
        "accepted": True,
        "review_error": None,
    }
    if mutation == "key":
        item["key"] = "other::0"
    elif mutation == "proposal":
        item["proposal"] = box("PL")
    elif mutation == "accepted":
        item["accepted"] = "false"
    responses = confirmation(item, item) if mutation == "duplicate" else confirmation(item)
    with pytest.raises(ValueError):
        evaluate_cached(source, responses)


def test_cli_writes_audit_and_never_overwrites_inputs(tmp_path, capsys):
    from pipe_inspector.evaluation.image_level import main

    source = tmp_path / "state.json"
    source.write_text(json.dumps(state(record(gt=[box("AJ")], raw=[box("AJ")]))))
    original = source.read_text()
    output = tmp_path / "results" / "image_level.json"
    main(["--state", str(source), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["inputs"]["state"] == str(source)
    assert report["variants"]["raw_yolo"]["image_level"]["mixed"]["micro"]["tp"] == 1
    assert source.read_text() == original
    with pytest.raises(SystemExit):
        main(["--state", str(source), "--output", str(source)])
    assert source.read_text() == original
    assert "overwrite" in capsys.readouterr().err
