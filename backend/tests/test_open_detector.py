from __future__ import annotations

import importlib
import math
import types
from pathlib import Path
from typing import Any

import pytest
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.vision import open_detector as detector_module

EXPECTED_VOCABULARY = (
    ("lateral connection opening in sewer pipe", DetectionLabel.AJ),
    ("pipe deformation", DetectionLabel.BX),
    ("pipe corrosion", DetectionLabel.FS),
    ("crack in sewer pipe", DetectionLabel.PL),
    ("pipe blockage", DetectionLabel.BLOCK),
    ("root intrusion", "OTHER"),
    ("water infiltration", "OTHER"),
)


class FakeTensor:
    def __init__(self, values: list[Any], owner: FakeResult) -> None:
        self._values = values
        self._owner = owner

    def detach(self) -> FakeTensor:
        return self

    def cpu(self) -> FakeTensor:
        return self

    def tolist(self) -> list[Any]:
        assert self._owner.on_cpu, "adapter must move vendor results to CPU before translation"
        return self._values


class FakeBoxes:
    def __init__(
        self,
        owner: FakeResult,
        *,
        xyxy: list[list[float]],
        conf: list[float],
        cls: list[float],
    ) -> None:
        self.xyxy = FakeTensor(xyxy, owner)
        self.conf = FakeTensor(conf, owner)
        self.cls = FakeTensor(cls, owner)


class FakeResult:
    def __init__(
        self,
        *,
        xyxy: list[list[float]] | None = None,
        conf: list[float] | None = None,
        cls: list[float] | None = None,
    ) -> None:
        self.orig_shape = (100, 200)
        self.on_cpu = False
        self.boxes = FakeBoxes(
            self,
            xyxy=xyxy
            or [
                [1.0, 2.0, 11.0, 12.0],
                [21.0, 2.0, 31.0, 12.0],
                [41.0, 2.0, 51.0, 12.0],
                [61.0, 2.0, 71.0, 12.0],
                [81.0, 2.0, 91.0, 12.0],
                [101.0, 2.0, 111.0, 12.0],
                [121.0, 2.0, 131.0, 12.0],
            ],
            conf=conf or [0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37],
            cls=cls or [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        )

    def cpu(self) -> FakeResult:
        self.on_cpu = True
        return self


class FakeUnderlyingModel:
    def __init__(self) -> None:
        self.embedding_calls: list[tuple[tuple[str, ...], bool]] = []

    def get_text_pe(self, names: list[str], *, cache_clip_model: bool) -> object:
        assert self.clip_model.encoder_id == "cpu-encoder"
        self.embedding_calls.append((tuple(names), cache_clip_model))
        return self.clip_model.embeddings


class FakeYOLOE:
    def __init__(self, result: FakeResult | None = None) -> None:
        self.model = FakeUnderlyingModel()
        self.predictor: object | None = None
        self.result = result or FakeResult()
        self.class_calls: list[tuple[tuple[str, ...], object]] = []
        self.predict_calls: list[dict[str, object]] = []
        self.cpu_calls = 0

    def set_classes(self, names: list[str], embeddings: object) -> None:
        self.class_calls.append((tuple(names), embeddings))

    def predict(self, **kwargs: object) -> list[FakeResult]:
        self.predict_calls.append(kwargs)
        self.predictor = object()
        return [self.result]

    def cpu(self) -> FakeYOLOE:
        assert self.predictor is None
        self.cpu_calls += 1
        return self


class FakeEncoder:
    encoder_id = "cpu-encoder"

    def __init__(self) -> None:
        self.embeddings = "fixed-embeddings"


def install_fake_vendor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[FakeResult] | None = None,
) -> tuple[list[FakeYOLOE], list[tuple[str, object]]]:
    models: list[FakeYOLOE] = []
    calls: list[tuple[str, object]] = []
    queued_results = list(results or [])

    def fake_yoloe(checkpoint: str, *, task: str) -> FakeYOLOE:
        calls.append(("yoloe", (checkpoint, task)))
        model = FakeYOLOE(queued_results.pop(0) if queued_results else None)
        models.append(model)
        return model

    def fake_mobileclip(*, device: str, weight: str) -> FakeEncoder:
        calls.append(("encoder", (device, weight)))
        return FakeEncoder()

    fake_ultralytics = types.SimpleNamespace(YOLOE=fake_yoloe)
    fake_text_model = types.SimpleNamespace(MobileCLIPTS=fake_mobileclip)
    fake_clip = types.SimpleNamespace()
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(empty_cache=lambda: calls.append(("empty_cache", None)))
    )

    def fake_import_module(name: str) -> object:
        calls.append(("import", name))
        return {
            "clip": fake_clip,
            "ultralytics": fake_ultralytics,
            "ultralytics.nn.text_model": fake_text_model,
            "torch": fake_torch,
        }[name]

    def fake_find_spec(name: str) -> object:
        calls.append(("find_spec", name))
        return object()

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    return models, calls


def local_assets(tmp_path: Path) -> tuple[Path, Path, Path]:
    checkpoint = tmp_path / "models" / "yoloe-26s-seg.pt"
    encoder = tmp_path / "models" / "mobileclip2_b.ts"
    image = tmp_path / "upload" / "frame.jpg"
    for path in (checkpoint, encoder, image):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return checkpoint, encoder, image


def test_constructor_is_io_free_and_pins_public_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_io(_path: Path) -> bool:
        raise AssertionError("constructor performed filesystem I/O")

    def unexpected_import(_name: str) -> object:
        raise AssertionError("constructor imported a model dependency")

    monkeypatch.setattr(Path, "is_file", unexpected_io)
    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    detector = detector_module.YOLOEOpenDetector(
        Path("/not/read/yoloe-26s-seg.pt"),
        Path("/not/read/mobileclip2_b.ts"),
        device="0",
        image_size=640,
        confidence=0.25,
    )

    assert detector.detector_id == "yoloe"
    assert detector.model_version == "yoloe-26s-seg.pt"
    assert detector.vocabulary_version == "sewer-open-v1"


@pytest.mark.parametrize("image_size", [0, -1])
def test_constructor_rejects_nonpositive_image_size(image_size: int) -> None:
    with pytest.raises(ValueError, match="image_size"):
        detector_module.YOLOEOpenDetector(
            Path("model.pt"), Path("encoder.ts"), image_size=image_size
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.nan, math.inf])
def test_constructor_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        detector_module.YOLOEOpenDetector(
            Path("model.pt"), Path("encoder.ts"), confidence=confidence
        )


def test_missing_upload_fails_before_model_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, _ = local_assets(tmp_path)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: (_ for _ in ()).throw(AssertionError("preflight should not run")),
    )
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(FileNotFoundError, match="input image"):
        detector.predict_image(str(tmp_path / "missing.jpg"))


@pytest.mark.parametrize(
    ("missing", "message"),
    [("checkpoint", "checkpoint"), ("encoder", "text encoder")],
)
def test_missing_local_model_asset_fails_without_importing_vendor(
    missing: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    (checkpoint if missing == "checkpoint" else encoder).unlink()
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(FileNotFoundError, match=message):
        detector.predict_image(image)


def test_missing_clip_dependency_fails_actionably_without_importing_ultralytics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    imports: list[str] = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(importlib, "import_module", lambda name: imports.append(name))
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(ImportError, match=r"pip install git\+https://github.com/ultralytics/CLIP.git"):
        detector.predict_image(image)

    assert imports == []


def test_broken_clip_dependency_fails_before_importing_ultralytics_text_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    imports: list[str] = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    def broken_clip_import(name: str) -> object:
        imports.append(name)
        if name == "clip":
            raise ImportError("broken local CLIP installation")
        raise AssertionError(f"vendor import must not run after broken CLIP: {name}")

    monkeypatch.setattr(importlib, "import_module", broken_clip_import)
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(ImportError, match="local CLIP tokenizer package"):
        detector.predict_image(image)

    assert imports == ["clip"]


@pytest.mark.parametrize("use_string_path", [False, True])
def test_prediction_uses_fixed_defect_vocabulary_and_returns_cpu_original_pixels(
    use_string_path: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    models, calls = install_fake_vendor(monkeypatch)
    detector = detector_module.YOLOEOpenDetector(
        checkpoint, encoder, device="0", image_size=640, confidence=0.25
    )

    batch = detector.predict_image(str(image) if use_string_path else image)

    model = models[0]
    expected_names = tuple(name for name, _ in EXPECTED_VOCABULARY)
    assert calls[:6] == [
        ("find_spec", "clip"),
        ("import", "clip"),
        ("import", "ultralytics"),
        ("yoloe", (str(checkpoint), "segment")),
        ("import", "ultralytics.nn.text_model"),
        ("encoder", ("cpu", str(encoder))),
    ]
    assert model.model.embedding_calls == [(expected_names, True)]
    assert model.class_calls == [(expected_names, "fixed-embeddings")]
    assert not hasattr(model.model, "clip_model")
    assert model.predict_calls == [
        {
            "source": str(image),
            "device": "0",
            "imgsz": 640,
            "conf": 0.25,
            "max_det": 100,
            "verbose": False,
        }
    ]
    assert batch.detector_id == "yoloe"
    assert batch.model_version == checkpoint.name
    assert batch.vocabulary_version == "sewer-open-v1"
    assert (batch.width, batch.height) == (200, 100)
    assert tuple((candidate.name, candidate.label) for candidate in batch.candidates) == (
        EXPECTED_VOCABULARY
    )
    assert [candidate.bbox_xyxy for candidate in batch.candidates] == [
        (1.0, 2.0, 11.0, 12.0),
        (21.0, 2.0, 31.0, 12.0),
        (41.0, 2.0, 51.0, 12.0),
        (61.0, 2.0, 71.0, 12.0),
        (81.0, 2.0, 91.0, 12.0),
        (101.0, 2.0, 111.0, 12.0),
        (121.0, 2.0, 131.0, 12.0),
    ]
    assert [candidate.score for candidate in batch.candidates] == pytest.approx(
        [0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37]
    )


def test_prediction_rejects_out_of_bounds_vendor_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    result = FakeResult(xyxy=[[1.0, 2.0, 201.0, 12.0]], conf=[0.7], cls=[0.0])
    install_fake_vendor(monkeypatch, results=[result])
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(ValueError, match="bbox"):
        detector.predict_image(image)


@pytest.mark.parametrize("class_id", [-1.0, 7.0, 1.5, math.nan])
def test_prediction_rejects_unknown_or_malformed_class_id(
    class_id: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    result = FakeResult(xyxy=[[1.0, 2.0, 11.0, 12.0]], conf=[0.7], cls=[class_id])
    install_fake_vendor(monkeypatch, results=[result])
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    with pytest.raises(ValueError, match="class id"):
        detector.predict_image(image)


def test_unload_is_idempotent_and_next_prediction_reloads_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, encoder, image = local_assets(tmp_path)
    models, calls = install_fake_vendor(monkeypatch)
    detector = detector_module.YOLOEOpenDetector(checkpoint, encoder)

    detector.unload()
    detector.predict_image(image)
    first = models[0]
    detector.unload()
    detector.unload()
    detector.predict_image(image)

    assert len(models) == 2
    assert first.cpu_calls == 1
    assert first.predictor is None
    assert calls.count(("empty_cache", None)) == 1
