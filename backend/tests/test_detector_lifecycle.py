from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
import torch
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.vision import detector as detector_module

TRAINED_NAMES = {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}


class FakeBoxes:
    xyxy = torch.tensor([[10.0, 20.0, 100.0, 200.0]], dtype=torch.float32)
    conf = torch.tensor([0.93179], dtype=torch.float32)
    cls = torch.tensor([4.0], dtype=torch.float32)


class FakeResult:
    orig_shape = (576, 720)
    boxes = FakeBoxes()


class FakeVendorModel:
    names = TRAINED_NAMES

    def __init__(self) -> None:
        self.predict_calls: list[dict[str, Any]] = []

    def predict(self, **kwargs: Any) -> list[FakeResult]:
        self.predict_calls.append(kwargs)
        return [FakeResult() for _ in kwargs["source"]]


class IndependentTestDetector:
    detector_id = "independent-test-detector"
    model_version = "fixture-v1"
    label_space_version = "sewer2403-5class-v1"
    class_names = TRAINED_NAMES

    def __init__(self) -> None:
        self.capabilities = detector_module.DetectorCapabilities(supports_batch=False)
        self.load_calls = 0
        self.warmup_calls = 0
        self.predict_calls = 0

    def load(self) -> None:
        self.load_calls += 1

    def warmup(self, image_path: str | Path) -> None:
        if not Path(image_path).is_file():
            raise FileNotFoundError(image_path)
        self.warmup_calls += 1

    def predict_image(self, path: str | Path) -> DetectionBatch:
        self.predict_calls += 1
        return canonical_batch(self.detector_id, self.model_version)

    def predict_batch(self, paths: Sequence[str | Path]) -> tuple[DetectionBatch, ...]:
        return tuple(self.predict_image(path) for path in paths)


def canonical_batch(detector_id: str, model_version: str) -> DetectionBatch:
    return DetectionBatch(
        detector_id=detector_id,
        model_version=model_version,
        label_space_version="sewer2403-5class-v1",
        width=720,
        height=576,
        detections=(
            Detection(
                label=DetectionLabel.AJ,
                native_label="AJ",
                score=0.93179,
                bbox_xyxy=(10.0, 20.0, 100.0, 200.0),
            ),
        ),
    )


def install_fake_vendor(monkeypatch: pytest.MonkeyPatch, model: FakeVendorModel) -> list[str]:
    loaded: list[str] = []
    module = types.ModuleType("ultralytics")

    def fake_yolo(checkpoint: str) -> FakeVendorModel:
        loaded.append(checkpoint)
        return model

    module.YOLO = fake_yolo
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    return loaded


def consume_detector(
    detector: detector_module.DetectorAdapter, image: Path
) -> tuple[object, DetectionBatch]:
    detector.load()
    detector.warmup(image)
    return detector.capabilities, detector.predict_image(image)


@pytest.mark.parametrize("implementation", ["ultralytics", "independent"])
def test_consumer_can_replace_detector_without_changing_canonical_contract(
    implementation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "representative.jpg"
    image.touch()
    if implementation == "ultralytics":
        checkpoint = tmp_path / "experiment" / "weights" / "best.pt"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.touch()
        vendor_model = FakeVendorModel()
        loaded = install_fake_vendor(monkeypatch, vendor_model)
        detector: Any = detector_module.UltralyticsDetector(checkpoint)
    else:
        vendor_model = None
        loaded = []
        detector = IndependentTestDetector()

    capabilities, batch = consume_detector(detector, image)

    assert capabilities.coordinate_space == "original_pixels"
    assert batch.width == 720
    assert batch.height == 576
    assert batch.detections[0].label is DetectionLabel.AJ
    assert batch.detections[0].score == pytest.approx(0.93179)
    assert batch.detections[0].bbox_xyxy == (10.0, 20.0, 100.0, 200.0)
    if implementation == "ultralytics":
        assert capabilities.supports_batch is True
        assert loaded == [str(checkpoint)]
        assert len(vendor_model.predict_calls) == 2
    else:
        assert capabilities.supports_batch is False
        assert detector.load_calls == 1
        assert detector.warmup_calls == 1
        assert detector.predict_calls == 1


def test_capabilities_are_immutable() -> None:
    capabilities = detector_module.DetectorCapabilities(supports_batch=True)

    with pytest.raises(FrozenInstanceError):
        capabilities.supports_batch = False


@pytest.mark.parametrize("operation", ["load", "warmup"])
def test_lifecycle_rejects_missing_checkpoint_before_vendor_loader(
    operation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "representative.jpg"
    image.touch()
    loaded = install_fake_vendor(monkeypatch, FakeVendorModel())
    detector = detector_module.UltralyticsDetector(tmp_path / "missing.pt")

    with pytest.raises(FileNotFoundError, match="checkpoint"):
        if operation == "load":
            detector.load()
        else:
            detector.warmup(image)

    assert loaded == []
