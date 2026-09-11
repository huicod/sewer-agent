import base64
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.evaluation.gallery import export_gallery
from pipe_inspector.evaluation.review import EvaluationManifest

SVG = {"svg": "http://www.w3.org/2000/svg"}


def _image(path: Path, *, colour: tuple[int, int, int]) -> bytes:
    Image.new("RGB", (64, 48), colour).save(path, format="PNG")
    return path.read_bytes()


def _manifest(tmp_path: Path, sample_ids: tuple[str, ...]) -> EvaluationManifest:
    samples = []
    for index, sample_id in enumerate(sample_ids):
        image = tmp_path / f"source-{index}.png"
        _image(image, colour=(index * 20, 40, 80))
        samples.append(
            {
                "sample_id": sample_id,
                "group_id": f"group-{index}",
                "image_path": str(image),
                "width": 64,
                "height": 48,
                "ground_truth": [{"label": "FS", "bbox_xyxy": [10, 12, 30, 32]}],
            }
        )
    return EvaluationManifest(
        dataset_name="fixed-gallery-fixture",
        split_provenance="operator supplied fixed split",
        samples=samples,
    )


def _record(sample_id: str, *, review_error: str | None = None) -> dict:
    return {
        "sample_id": sample_id,
        "observation": {
            "detection": {
                "detector_id": "fixture-detector",
                "model_version": "fixture-v1",
                "label_space_version": "sewer2403-5class-v1",
                "width": 64,
                "height": 48,
                "detections": [
                    {
                        "label": "PL",
                        "native_label": "PL",
                        "score": 0.91,
                        "bbox_xyxy": [4, 5, 24, 25],
                    }
                ],
            },
            "quality": {
                "mean_luminance": 90,
                "contrast": 20,
                "sharpness": 40,
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
                    "reason": "形态更符合破损",
                }
            ],
            "additional_anomalies": [],
            "summary": "候选应改类",
        },
        "review_error": review_error,
        "predictions": [{"label": "FS", "bbox_xyxy": [10, 12, 30, 32], "score": 0.8}],
        "changes": [
            {
                "index": 0,
                "action": "relabel",
                "label": "FS",
                "bbox_xyxy": [10, 12, 30, 32],
                "reason": "形态更符合破损",
            }
        ],
        # A conflicting legacy field proves that the gallery uses the exact scored contract.
        "decision": {"findings": []},
    }


def _box_attributes(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    rectangle = root.find(".//svg:rect[@data-box-index='0']", SVG)
    assert rectangle is not None
    return rectangle.attrib


def test_only_yolo_displays_model_score_not_fused_ranking_or_gt_score(tmp_path):
    fixed = _manifest(tmp_path, ("first",))
    export_gallery(fixed, [_record("first")], tmp_path / "gallery")
    sample_dir = tmp_path / "gallery" / "sample-0001"
    for layer in ("fused", "ground-truth"):
        root = ET.parse(sample_dir / f"{layer}.svg").getroot()
        label = root.find("svg:text[@data-box-index='0']", SVG).text
        assert label == "#0 FS"
    root = ET.parse(sample_dir / "yolo.svg").getroot()
    assert root.find("svg:text[@data-box-index='0']", SVG).text == "#0 PL 0.910"


def test_image_level_pending_and_incomplete_audit_are_visible(tmp_path):
    fixed = _manifest(tmp_path, ("first",))
    record = _record("first")
    record["pending"] = True
    record["review"]["full_image_audited"] = False
    export_gallery(fixed, [record], tmp_path / "gallery")
    detail = (tmp_path / "gallery" / "sample-0001" / "index.html").read_text()
    assert "图像级待审" in detail
    assert "全图审计：未完成" in detail


def test_protected_disagreement_displays_vlm_proposal_separately_from_kept_class(tmp_path):
    from pipe_inspector.contracts.inspection import ImageObservation
    from pipe_inspector.evaluation.guided import fuse_guided

    fixed = _manifest(tmp_path, ("first",))
    record = _record("first")
    record["review"]["candidates"][0]["evidence_strength"] = "clear"
    record.update(
        fuse_guided(
            ImageObservation.model_validate(record["observation"]),
            GuidedReview.model_validate(record["review"]),
        )
    )
    export_gallery(fixed, [record], tmp_path / "gallery")
    directory = tmp_path / "gallery" / "sample-0001"
    detail = (directory / "index.html").read_text()
    assert "VLM 裁决：revise" in detail
    assert "VLM 建议类别：FS" in detail
    assert "retain_high_confidence" in detail
    assert _box_attributes(directory / "fused.svg")["data-label"] == "PL"


def test_export_copies_manifest_images_and_draws_exact_scored_boxes(tmp_path) -> None:
    fixed = _manifest(tmp_path, ("first", "second"))
    originals = [sample.image_path.read_bytes() for sample in fixed.samples]
    guided_record = _record("first")
    guided_record["review"]["candidates"][0]["evidence_strength"] = "clear"
    guided_record["review"]["additional_anomalies"] = [
        {
            "label": "PL",
            "bbox_xyxy": [1, 2, 9, 10],
            "reason": "可定位但被融合门控抑制",
            "evidence_strength": "uncertain",
        },
        {
            "label": "OTHER",
            "bbox_xyxy": [30, 2, 40, 12],
            "reason": "五类外现象",
            "evidence_strength": "clear",
        },
        {
            "label": "BX",
            "bbox_xyxy": None,
            "reason": "无法定位",
            "evidence_strength": "uncertain",
        },
    ]
    guided_record["review"] = GuidedReview.model_validate(guided_record["review"]).model_dump(
        mode="json"
    )
    records = [_record("second"), guided_record]

    index_path = export_gallery(fixed, records, tmp_path / "gallery")

    assert index_path == tmp_path / "gallery" / "index.html"
    assert index_path.is_file()
    for index, expected in enumerate(originals, start=1):
        sample_dir = tmp_path / "gallery" / f"sample-{index:04d}"
        assert (sample_dir / "original.png").read_bytes() == expected
        assert (sample_dir / "record.json").is_file()
        assert (sample_dir / "review.json").is_file()
        assert (sample_dir / "fusion.json").is_file()
        assert (sample_dir / "yolo.svg").is_file()
        assert (sample_dir / "vlm-suggestions.svg").is_file()
        assert (sample_dir / "fused.svg").is_file()
        assert (sample_dir / "ground-truth.svg").is_file()

    fused = _box_attributes(tmp_path / "gallery" / "sample-0001" / "fused.svg")
    assert {
        "x": "10",
        "y": "12",
        "width": "20",
        "height": "20",
        "data-label": "FS",
    }.items() <= fused.items()
    yolo = _box_attributes(tmp_path / "gallery" / "sample-0001" / "yolo.svg")
    assert {
        "x": "4",
        "y": "5",
        "width": "20",
        "height": "20",
        "data-label": "PL",
    }.items() <= yolo.items()
    suggestions = _box_attributes(tmp_path / "gallery" / "sample-0001" / "vlm-suggestions.svg")
    assert {
        "x": "1",
        "y": "2",
        "width": "8",
        "height": "8",
        "data-label": "PL",
    }.items() <= suggestions.items()
    assert "data-score" not in suggestions

    yolo_root = ET.parse(tmp_path / "gallery" / "sample-0001" / "yolo.svg").getroot()
    yolo_label = yolo_root.find(".//svg:text[@data-box-index='0']", SVG)
    assert yolo_label is not None
    assert yolo_label.attrib["y"] == "14"
    assert yolo_label.text == "#0 PL 0.910"

    root = ET.parse(tmp_path / "gallery" / "sample-0001" / "fused.svg").getroot()
    assert root.attrib["viewBox"] == "0 0 64 48"
    image = root.find("svg:image", SVG)
    assert image is not None
    encoded = image.attrib.pop("href")
    assert encoded.startswith("data:image/png;base64,")
    assert base64.b64decode(encoded.partition(",")[2]) == originals[0]
    assert image.attrib == {
        "x": "0",
        "y": "0",
        "width": "64",
        "height": "48",
        "preserveAspectRatio": "none",
    }

    detail = (tmp_path / "gallery" / "sample-0001" / "index.html").read_text(encoding="utf-8")
    index = index_path.read_text(encoding="utf-8")
    assert 'sample-0001/review.json">VLM 建议（像素坐标）' in index
    assert 'sample-0001/fusion.json">采纳融合 JSON' in index
    assert "VLM 新增建议（融合前，非最终预测）" in detail
    assert "可定位但被融合门控抑制" in detail
    assert "未绘制：OTHER 不属于五类" in detail
    assert "未绘制：未定位" in detail
    assert "review 无效" not in detail
    assert 'data-stage="yolo" data-tp="0" data-fp="1" data-fn="1"' in detail
    assert 'data-stage="fused" data-tp="1" data-fp="0" data-fn="0"' in detail


def test_export_covers_missing_records_and_missing_source_images(tmp_path) -> None:
    fixed = _manifest(tmp_path, ("recorded", "no-record", "no-source"))
    fixed.samples[2].image_path.unlink()
    output = tmp_path / "new-output"

    index_path = export_gallery(fixed, [_record("recorded", review_error="timeout")], output)

    index = index_path.read_text(encoding="utf-8")
    assert 'data-expected="3" data-recorded="1" data-missing="2"' in index
    assert "复核失败：timeout" in index
    assert index.count("查看详情") == 3
    assert (output / "sample-0002" / "original.png").is_file()
    assert "记录缺失" in (output / "sample-0002" / "index.html").read_text(encoding="utf-8")
    assert "记录缺失" in (output / "sample-0002" / "fused.svg").read_text(encoding="utf-8")
    assert (output / "sample-0003" / "original-unavailable.svg").is_file()
    assert "原图缺失" in (output / "sample-0003" / "index.html").read_text(encoding="utf-8")


def test_empty_records_still_export_every_manifest_sample(tmp_path) -> None:
    fixed = _manifest(tmp_path, ("one", "two"))

    index_path = export_gallery(fixed, [], tmp_path / "gallery")

    index = index_path.read_text(encoding="utf-8")
    assert 'data-expected="2" data-recorded="0" data-missing="2"' in index
    assert sorted(path.name for path in index_path.parent.glob("sample-*")) == [
        "sample-0001",
        "sample-0002",
    ]


def test_export_escapes_html_and_xml_without_using_ids_as_paths(tmp_path) -> None:
    hostile_id = '<img src=x onerror="alert(1)">../../escape'
    fixed = _manifest(tmp_path, (hostile_id,))
    values = fixed.model_dump(mode="json")
    values["dataset_name"] = "<script>dataset()</script>"
    fixed = EvaluationManifest.model_validate(values)
    record = _record(hostile_id, review_error="<b>provider</b>")
    record["review"]["summary"] = "</textarea><script>review()</script>"
    record["changes"][0].update(
        action="<svg onload=action()>",
        label="FS&<bad>",
        reason="</li><script>reason()</script>",
        pending=True,
        additional_index=3,
        gate="<script>gate()</script>",
    )

    export_gallery(fixed, [record], tmp_path / "gallery")

    index = (tmp_path / "gallery" / "index.html").read_text(encoding="utf-8")
    detail = (tmp_path / "gallery" / "sample-0001" / "index.html").read_text(encoding="utf-8")
    svg = (tmp_path / "gallery" / "sample-0001" / "fused.svg").read_text(encoding="utf-8")
    assert "<script>dataset()" not in index
    assert "&lt;script&gt;dataset()&lt;/script&gt;" in index
    assert '<img src=x onerror="alert(1)">' not in index + detail + svg
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;../../escape" in detail
    assert "<script>review()" not in detail
    assert "&lt;/textarea&gt;&lt;script&gt;review()&lt;/script&gt;" in detail
    assert "<script>reason()" not in detail
    assert "&lt;/li&gt;&lt;script&gt;reason()&lt;/script&gt;" in detail
    assert "<svg onload=action()>" not in detail
    assert "新增序号：3" in detail
    assert "&lt;script&gt;gate()&lt;/script&gt;" in detail
    assert "待审" in detail
    assert not (tmp_path / "escape").exists()
