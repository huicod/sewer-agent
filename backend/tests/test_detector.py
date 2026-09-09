from __future__ import annotations

import math
import sys
import types
from pathlib import Path

import pytest
import torch
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.vision.detector import UltralyticsDetector
from pydantic import ValidationError

TRAINED_NAMES = {0: "BLOCK", 1: "PL", 2: "AJ", 3: "BX", 4: "FS"}


class FakeBoxes:
    def __init__(self, *, xyxy: list[list[float]], conf: list[float], cls: list[float]):
        self.xyxy = torch.tensor(xyxy, dtype=torch.float32)
        self.conf = torch.tensor(conf, dtype=torch.float32)
        self.cls = torch.tensor(cls, dtype=torch.float32)


class FakeResult:
    def __init__(self, boxes: FakeBoxes):
        self.orig_shape = (100, 200)
        self.boxes = boxes


class FakeModel:
    def __init__(self, names: dict[int, str] | None = None):
        self.names = TRAINED_NAMES if names is None else names
        self.calls: list[dict[str, object]] = []

    def predict(self, *, source: list[str], **kwargs: object) -> list[FakeResult]:
        self.calls.append({"source": source, **kwargs})
        return [
            FakeResult(FakeBoxes(xyxy=[[1.0, 2.0, 10.0, 20.0]], conf=[0.75], cls=[2.0]))
            for _ in source
        ]


def install_fake_ultralytics(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> list[str]:
    loaded: list[str] = []
    module = types.ModuleType("ultralytics")
    module.__version__ = "8.4.46"

    def fake_yolo(checkpoint: str) -> FakeModel:
        loaded.append(checkpoint)
        return model

    module.YOLO = fake_yolo
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    return loaded


def valid_batch(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "detector_id": "ultralytics-yolo26",
        "model_version": "run/best",
        "label_space_version": "sewer2403-5class-v1",
        "width": 200,
        "height": 100,
        "detections": [
            {
                "label": "AJ",
                "native_label": "AJ",
                "score": 0.75,
                "bbox_xyxy": (1.0, 2.0, 10.0, 20.0),
            }
        ],
    }
    values.update(overrides)
    return values


def test_detection_batch_accepts_canonical_original_pixel_box() -> None:
    batch = DetectionBatch.model_validate(valid_batch())

    assert batch.detections[0] == Detection(
        label=DetectionLabel.AJ,
        native_label="AJ",
        score=0.75,
        bbox_xyxy=(1.0, 2.0, 10.0, 20.0),
    )


@pytest.mark.parametrize("label", ["NORMAL", "UNCERTAIN", "unknown"])
def test_detection_rejects_noncanonical_object_labels(label: str) -> None:
    with pytest.raises(ValidationError):
        Detection(
            label=label,
            native_label=label,
            score=0.5,
            bbox_xyxy=(1.0, 2.0, 10.0, 20.0),
        )


@pytest.mark.parametrize("score", [-0.01, 1.01, math.nan, math.inf])
def test_detection_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(ValidationError):
        Detection(
            label="PL",
            native_label="PL",
            score=score,
            bbox_xyxy=(1.0, 2.0, 10.0, 20.0),
        )


@pytest.mark.parametrize(
    "bbox",
    [
        (10.0, 2.0, 1.0, 20.0),
        (1.0, 20.0, 10.0, 2.0),
        (-1.0, 2.0, 10.0, 20.0),
        (1.0, 2.0, 201.0, 20.0),
        (1.0, 2.0, 10.0, 101.0),
        (1.0, 2.0, math.inf, 20.0),
    ],
)
def test_detection_batch_rejects_unordered_nonfinite_or_out_of_bounds_boxes(
    bbox: tuple[float, float, float, float],
) -> None:
    data = valid_batch()
    detection = dict(data["detections"][0])  # type: ignore[index]
    detection["bbox_xyxy"] = bbox
    data["detections"] = [detection]

    with pytest.raises(ValidationError):
        DetectionBatch.model_validate(data)


def test_adapter_loads_lazily_and_translates_class_id_through_model_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "experiment" / "weights" / "best.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.touch()
    image = tmp_path / "sample.jpg"
    image.touch()
    model = FakeModel()
    loaded = install_fake_ultralytics(monkeypatch, model)
    detector = UltralyticsDetector(checkpoint, device="0", imgsz=960, conf=0.25)

    assert loaded == []
    batch = detector.predict_image(image)

    assert loaded == [str(checkpoint)]
    assert batch.width == 200
    assert batch.height == 100
    assert batch.model_version == "experiment/best"
    assert detector.class_names == TRAINED_NAMES
    assert batch.detections == (
        Detection(
            label=DetectionLabel.AJ,
            native_label="AJ",
            score=0.75,
            bbox_xyxy=(1.0, 2.0, 10.0, 20.0),
        ),
    )
    assert model.calls == [
        {
            "source": [str(image)],
            "device": "0",
            "imgsz": 960,
            "conf": 0.25,
            "verbose": False,
        }
    ]

    detector.predict_image(image)
    assert loaded == [str(checkpoint)]


def test_adapter_rejects_model_with_noncanonical_class_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "best.pt"
    checkpoint.touch()
    image = tmp_path / "sample.jpg"
    image.touch()
    names = dict(TRAINED_NAMES)
    names[2] = "NORMAL"
    install_fake_ultralytics(monkeypatch, FakeModel(names))
    detector = UltralyticsDetector(checkpoint)

    with pytest.raises(ValueError, match="class map"):
        detector.predict_image(image)


def test_adapter_rejects_missing_input_before_loading_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "best.pt"
    checkpoint.touch()
    loaded = install_fake_ultralytics(monkeypatch, FakeModel())
    detector = UltralyticsDetector(checkpoint)

    with pytest.raises(FileNotFoundError, match="input image"):
        detector.predict_image(tmp_path / "missing.jpg")

    assert loaded == []

def test_class_names_rejects_missing_checkpoint_before_vendor_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = install_fake_ultralytics(monkeypatch, FakeModel())
    detector = UltralyticsDetector(tmp_path / "missing.pt")

    with pytest.raises(FileNotFoundError, match="checkpoint"):
        _ = detector.class_names

    assert loaded == []
