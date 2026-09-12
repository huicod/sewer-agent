import io
import json

import pytest
from PIL import Image
from test_enhancement import Model


@pytest.fixture
def inputs(tmp_path):
    image = tmp_path / "input.png"
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), (60, 100, 160)).save(stream, format="PNG")
    image.write_bytes(stream.getvalue())
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"sample_id": "fixture", "image": str(image), "roi": [2, 3, 10, 12]}])
    )
    models = tmp_path / "models"
    models.mkdir()
    for name in (
        "dehazeformer-mct-8e0b26c7.pth",
        "restormer-motion-v1.0.pth",
        "realesr-general-x4v3-v0.2.5.0.pth",
    ):
        (models / name).write_bytes(b"fixture-not-a-model")
    return manifest, models, tmp_path / "output", image


def test_gallery_contains_real_tool_outputs_and_preserves_original(inputs):
    from pipe_inspector.evaluation.enhancement import evaluate

    manifest, models, output, source = inputs
    original = source.read_bytes()
    summary = evaluate(
        manifest,
        models,
        output,
        model_factory=lambda kind, checkpoint, device: Model(
            kind, scale=4 if kind == "super_resolution" else 1
        ),
    )
    assert summary["succeeded"] == 3 and summary["failed"] == 0
    assert source.read_bytes() == original
    records = json.loads((output / "results.json").read_text())["samples"]
    assert records[0]["sample_id"] == "fixture"
    assert records[0]["automatic_gate"]["should_run"] is False
    for kind, size in (("dehaze", (32, 24)), ("deblur", (32, 24)), ("super_resolution", (32, 36))):
        assert records[0]["tools"][kind]["status"] == "succeeded"
        with Image.open(output / "fixture" / f"{kind}.png") as image:
            assert image.size == size
    html = (output / "index.html").read_text()
    assert "fixture/deblur.png" in html
    assert "fixture/roi.png" in html
    assert "准确率" in html
    with pytest.raises(FileExistsError):
        evaluate(manifest, models, output)


def test_one_model_failure_is_visible_and_not_replaced_by_fake_success(inputs):
    from pipe_inspector.evaluation.enhancement import evaluate

    manifest, models, output, _ = inputs
    summary = evaluate(
        manifest,
        models,
        output,
        model_factory=lambda kind, checkpoint, device: Model(
            kind,
            scale=4 if kind == "super_resolution" else 1,
            invalid="failure" if kind == "deblur" else None,
        ),
    )
    assert summary["succeeded"] == 2 and summary["failed"] == 1
    assert not (output / "fixture" / "deblur.png").exists()
    record = json.loads((output / "results.json").read_text())["samples"][0]["tools"]["deblur"]
    assert record["status"] == "failed" and record["error_code"]
    assert "失败" in (output / "index.html").read_text()


@pytest.mark.parametrize("invalid", ["id", "roi", "duplicate"])
def test_invalid_manifest_creates_no_output(inputs, invalid):
    from pipe_inspector.evaluation.enhancement import evaluate

    manifest, models, output, _ = inputs
    samples = json.loads(manifest.read_text())
    if invalid == "id":
        samples[0]["sample_id"] = "../outside"
    elif invalid == "roi":
        samples[0]["roi"] = [0, 0, 33, 24]
    else:
        samples += samples
    manifest.write_text(json.dumps(samples))
    with pytest.raises(ValueError):
        evaluate(manifest, models, output)
    assert not output.exists()
