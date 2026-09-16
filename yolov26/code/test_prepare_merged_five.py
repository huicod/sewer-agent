"""Real image/XML fixtures for label cleaning and split isolation."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))


def module():
    import prepare_merged_five

    return prepare_merged_five


def fixture(tmp_path, name="a", *, filename=None, objects=None, size=(80, 60)):
    image = tmp_path / f"{name}.png"
    Image.fromarray(np.random.default_rng(42).integers(0, 256, (60, 80, 3), dtype=np.uint8)).save(
        image
    )
    xml = tmp_path / f"{name}.xml"
    objects = objects if objects is not None else [("PL", (10, 10, 40, 40))]
    body = "".join(
        f"<object><name>{label}</name><bndbox>"
        + "".join(
            f"<{key}>{value}</{key}>" for key, value in zip(("xmin", "ymin", "xmax", "ymax"), box)
        )
        + "</bndbox></object>"
        for label, box in objects
    )
    xml.write_text(
        f"<annotation><filename>{filename or image.name}</filename>"
        f"<size><width>{size[0]}</width><height>{size[1]}</height></size>{body}</annotation>"
    )
    return image, xml


def test_target_mapping_clips_one_pixel_and_deduplicates_boxes(tmp_path):
    m = module()
    image, xml = fixture(
        tmp_path,
        objects=[("CJ", (-1, 0, 81, 60)), ("CJ", (-1, 0, 81, 60)), ("FS", (10, 10, 40, 40))],
    )
    row = m.read_sample(image, xml, "sewer2403")
    assert [b.class_id for b in row.labels] == [3, 2]
    assert row.labels[0].width == 1.0
    assert row.repairs == {"clipped_boxes": 2, "duplicate_boxes": 1}


def test_sedd_mapping_does_not_turn_roots_into_block(tmp_path):
    m = module()
    image, xml = fixture(
        tmp_path,
        objects=[
            ("crack", (10, 10, 40, 40)),
            ("deposit", (0, 40, 80, 60)),
            ("root", (0, 0, 10, 10)),
        ],
    )
    row = m.read_sample(image, xml, "sedd")
    assert [b.class_id for b in row.labels] == [0, 3]
    assert row.source_classes == ("crack", "deposit", "root")
    _, xml = fixture(tmp_path, objects=[("root", (0, 0, 10, 10))])
    with pytest.raises(m.Rejected, match="sedd_without_target"):
        m.read_sample(image, xml, "sedd")


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"filename": "wrong.png"}, "filename_mismatch"),
        ({"size": (160, 60)}, "size_mismatch"),
        ({"objects": [("PL", (0, 0, 200, 20))]}, "box_out_of_bounds"),
        ({"objects": [("PL", (10, 10, 10, 40))]}, "invalid_box"),
        ({"objects": [("PL", (0, 0, float("nan"), 20))]}, "invalid_box"),
        ({"objects": [("surprise", (0, 0, 10, 10))]}, "unknown_class"),
        ({"filename": "testmosaicmix-1.png", "name": "testmosaicmix-1"}, "preaugmented_image"),
    ],
)
def test_unsafe_samples_are_quarantined_not_silently_background(tmp_path, kwargs, reason):
    m = module()
    image, xml = fixture(tmp_path, **kwargs)
    with pytest.raises(m.Rejected, match=reason):
        m.read_sample(image, xml, "sewer2403")


def test_renamed_roboflow_file_recovers_original_video_identity(tmp_path):
    m = module()
    identifier = "a" * 32
    image, xml = fixture(
        tmp_path, name=identifier, filename=f"pipe-mp4-t-1-0_png.rf.{identifier}.png"
    )
    row = m.read_sample(image, xml, "sewer2403")
    assert "sewer2403:video:pipe-mp4" in row.aliases
    assert row.repairs["recovered_filename"] == 1


def test_non_target_labels_remain_explicit_background_not_normal(tmp_path):
    m = module()
    image, xml = fixture(tmp_path, objects=[("SG", (0, 0, 10, 10))])
    row = m.read_sample(image, xml, "sewer2403")
    assert row.labels == ()
    assert row.background_kind == "non_target_only"


def test_duplicate_conflict_is_quarantined_and_identical_copy_is_removed(tmp_path):
    m = module()
    a = m.read_sample(*fixture(tmp_path, "a"), "sewer2403")
    b = m.read_sample(*fixture(tmp_path, "b"), "sewer2403")
    c = m.read_sample(*fixture(tmp_path, "c", objects=[("FS", (10, 10, 40, 40))]), "sewer2403")
    kept, rejected = m.deduplicate([a, b])
    assert len(kept) == 1
    assert rejected[0]["reason"] == "exact_duplicate"
    kept, rejected = m.deduplicate([a, b, c])
    assert kept == []
    assert len(rejected) == 3
    assert {r["reason"] for r in rejected} == {"duplicate_annotation_conflict"}


def test_visual_and_filename_groups_are_transitive():
    m = module()
    assert m.connected_groups([("a",), ("a", "b"), ("b",), ("c",)], [(2, 3)]) == [0, 0, 0, 0]


def test_split_keeps_groups_and_all_classes_then_exports(tmp_path):
    m = module()
    rows = []
    for index in range(100):
        image, xml = fixture(
            tmp_path,
            str(index),
            objects=[(("PL", "BX", "FS", "CJ", "AJ")[index % 5], (10, 10, 40, 40))],
        )
        rows.append(m.read_sample(image, xml, "sewer2403"))
    groups = list(range(100))
    groups[1] = groups[0]
    splits = m.split_rows(rows, groups, seed=7)
    assert splits[0] == splits[1]
    assert {s: splits.count(s) for s in ("train", "val", "test")} == {
        "train": 90,
        "val": 5,
        "test": 5,
    }
    for split in ("val", "test"):
        assert {r.labels[0].class_id for r, s in zip(rows, splits) if s == split} == set(range(5))
    assert splits == m.split_rows(rows, groups, seed=7)
    out = tmp_path / "output"
    m.export_dataset(out, rows, groups, splits, [], [], seed=7)
    config = yaml.safe_load((out / "data.yaml").read_text())
    assert config["names"] == {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}
    assert len(list((out / "labels/val").glob("*.txt"))) == 5
    assert len(list((out / "images/train").glob("*.png"))) == 90
    with pytest.raises(FileExistsError):
        m.export_dataset(out, rows, groups, splits, [], [], seed=7)


def test_visual_pairing_keeps_near_copies_but_not_unrelated():
    m = module()
    rng = np.random.default_rng(17)
    a = rng.integers(30, 210, (32, 32, 3), dtype=np.uint8)
    b = a + 1
    c = rng.integers(30, 210, (32, 32, 3), dtype=np.uint8)
    assert m.visual_pairs([a, b, c]) == [(0, 1)]


def test_preparation_saves_clean_labels_without_deciding_splits(tmp_path):
    m = module()
    row = m.read_sample(*fixture(tmp_path), "sewer2403")
    out = tmp_path / "prepared"
    m.write_preparation(out, [row], [0], [], [])
    data = json.loads((out / "cleaned_manifest.jsonl").read_text())
    assert data["labels"][0]["class_id"] == 0
    assert data["labels"][0]["width"] == 0.375
    assert data["group_id"] == 0
    assert "split" not in data
    assert not (out / "data.yaml").exists()
    m.write_gallery(out, [row], ["unassigned"])
    assert "unassigned" in (out / "preview/index.html").read_text()
    assert (out / "preview/sewer2403__a.jpg").is_file()
    with pytest.raises(FileExistsError):
        m.write_preparation(out, [row], [0], [], [])


def test_final_selection_excludes_other_only_and_mixed_keeps_empty(tmp_path):
    m = module()
    rows = [
        m.read_sample(*fixture(tmp_path, "positive"), "sewer2403"),
        m.read_sample(*fixture(tmp_path, "empty", objects=[]), "sewer2403"),
        m.read_sample(*fixture(tmp_path, "other", objects=[("SG", (0, 0, 10, 10))]), "sewer2403"),
        m.read_sample(
            *fixture(tmp_path, "mixed", objects=[("PL", (10, 10, 40, 40)), ("SG", (0, 0, 10, 10))]),
            "sewer2403",
        ),
    ]
    kept, rejected = m.select_five_only(rows)
    assert [r.image.stem for r in kept] == ["positive", "empty"]
    assert {r["reason"] for r in rejected} == {"non_target_only", "mixed_non_target"}


def test_reload_preserves_group_even_when_linking_sample_is_excluded(tmp_path):
    m = module()
    a = m.read_sample(*fixture(tmp_path, "a"), "sewer2403")
    b = m.read_sample(*fixture(tmp_path, "b"), "sewer2403")
    out = tmp_path / "prepared"
    m.write_preparation(out, [a, b], [7, 7], [], [])
    rows, rejected = m.load_preparation(out)
    assert rejected == []
    assert rows[0].labels == a.labels
    assert m.connected_groups([r.aliases for r in rows], []) == [0, 0]


def test_export_writes_source_specific_eval_lists_without_renumbering(tmp_path):
    m = module()
    sewer = m.read_sample(*fixture(tmp_path, "sewer"), "sewer2403")
    sedd = m.read_sample(*fixture(tmp_path, "sedd", objects=[("deposit", (0, 0, 10, 10))]), "sedd")
    out = tmp_path / "out"
    m.export_dataset(out, [sewer, sedd], [0, 1], ["val", "test"], [], [], seed=7)
    assert "sewer2403__sewer.png" in (out / "eval/sewer2403_val.txt").read_text()
    assert "sedd__sedd.png" in (out / "eval/sedd_test.txt").read_text()
    assert "3 " == (out / "labels/test/sedd__sedd.txt").read_text()[:2]
    metadata = json.loads((out / "summary.json").read_text())
    assert metadata["evaluation_scope"]["sedd"] == ["PL", "BLOCK"]


def test_split_balances_sources_even_when_they_share_a_common_class(tmp_path):
    m = module()
    base = m.read_sample(*fixture(tmp_path), "sewer2403")
    rows = [replace(base, dataset="sewer2403" if i < 200 else "sedd") for i in range(250)]
    splits = m.split_rows(rows, list(range(250)), seed=20260916)
    for split in ("val", "test"):
        assert 1 <= sum(r.dataset == "sedd" and s == split for r, s in zip(rows, splits)) <= 4
        assert 8 <= sum(r.dataset == "sewer2403" and s == split for r, s in zip(rows, splits)) <= 12
