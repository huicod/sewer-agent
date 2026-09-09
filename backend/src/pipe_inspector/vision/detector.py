from __future__ import annotations

import importlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel

DETECTOR_ID = "ultralytics-yolo26"
LABEL_SPACE_VERSION = "sewer2403-5class-v1"
TRAINED_LABELS = frozenset(label.value for label in DetectionLabel)


class DetectorAdapter(Protocol):
    detector_id: str
    model_version: str
    label_space_version: str

    @property
    def class_names(self) -> dict[int, str]: ...

    def predict_image(self, path: str | Path) -> DetectionBatch: ...

    def predict_batch(self, paths: Sequence[str | Path]) -> tuple[DetectionBatch, ...]: ...


class UltralyticsDetector:
    def __init__(
        self,
        checkpoint: str | Path,
        *,
        device: str = "0",
        imgsz: int = 960,
        conf: float = 0.25,
    ) -> None:
        if imgsz <= 0:
            raise ValueError("imgsz must be positive")
        if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
            raise ValueError("conf must be finite and between 0 and 1")
        self.checkpoint = Path(checkpoint)
        self.device = device
        self.imgsz = imgsz
        self.conf = conf
        self.detector_id = DETECTOR_ID
        self.label_space_version = LABEL_SPACE_VERSION
        self.model_version = self._derive_model_version(self.checkpoint)
        self._model: Any | None = None
        self._class_names: dict[int, str] | None = None

    @staticmethod
    def _derive_model_version(checkpoint: Path) -> str:
        if checkpoint.parent.name == "weights":
            return f"{checkpoint.parent.parent.name}/{checkpoint.stem}"
        return checkpoint.name

    @property
    def class_names(self) -> dict[int, str]:
        self._ensure_model()
        return dict(self._class_names or {})

    def predict_image(self, path: str | Path) -> DetectionBatch:
        return self.predict_batch((path,))[0]

    def predict_batch(self, paths: Sequence[str | Path]) -> tuple[DetectionBatch, ...]:
        image_paths = tuple(Path(path) for path in paths)
        if not image_paths:
            return ()
        for image_path in image_paths:
            if not image_path.is_file():
                raise FileNotFoundError(f"input image does not exist: {image_path}")
        model = self._ensure_model()
        results = tuple(
            model.predict(
                source=[str(path) for path in image_paths],
                device=self.device,
                imgsz=self.imgsz,
                conf=self.conf,
                verbose=False,
            )
        )
        if len(results) != len(image_paths):
            raise RuntimeError(
                f"detector returned {len(results)} results for {len(image_paths)} images"
            )
        return tuple(self._translate_result(result) for result in results)

    def _ensure_model(self) -> Any:
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"checkpoint does not exist: {self.checkpoint}")
        if self._model is None:
            ultralytics = importlib.import_module("ultralytics")
            model = ultralytics.YOLO(str(self.checkpoint))
            class_names = self._normalize_class_names(model.names)
            if (
                len(class_names) != len(TRAINED_LABELS)
                or set(class_names.values()) != TRAINED_LABELS
            ):
                raise ValueError(
                    "model class map must contain exactly AJ, BX, FS, PL, and BLOCK; "
                    f"got {class_names}"
                )
            self._model = model
            self._class_names = class_names
        return self._model

    @staticmethod
    def _normalize_class_names(names: object) -> dict[int, str]:
        if isinstance(names, Mapping):
            return {int(class_id): str(label) for class_id, label in names.items()}
        if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
            return {class_id: str(label) for class_id, label in enumerate(names)}
        raise ValueError(f"unsupported model class map: {names!r}")

    def _translate_result(self, result: Any) -> DetectionBatch:
        height, width = (int(value) for value in result.orig_shape)
        detections: list[Detection] = []
        boxes = result.boxes
        if boxes is not None:
            xyxy = self._tensor_values(boxes.xyxy)
            scores = self._tensor_values(boxes.conf)
            class_ids = self._tensor_values(boxes.cls)
            if not (len(xyxy) == len(scores) == len(class_ids)):
                raise ValueError("detector box, score, and class arrays differ in length")
            for coordinates, score, raw_class_id in zip(xyxy, scores, class_ids, strict=True):
                class_number = float(raw_class_id)
                if not math.isfinite(class_number) or not class_number.is_integer():
                    raise ValueError(f"invalid detector class id: {raw_class_id!r}")
                class_id = int(class_number)
                try:
                    native_label = (self._class_names or {})[class_id]
                except KeyError as exc:
                    raise ValueError(f"detector returned unknown class id: {class_id}") from exc
                coordinate_values = tuple(float(value) for value in coordinates)
                if len(coordinate_values) != 4:
                    raise ValueError(f"detector returned malformed bbox: {coordinate_values!r}")
                detections.append(
                    Detection(
                        label=DetectionLabel(native_label),
                        native_label=native_label,
                        score=float(score),
                        bbox_xyxy=coordinate_values,
                    )
                )
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=width,
            height=height,
            detections=tuple(detections),
        )

    @staticmethod
    def _tensor_values(value: Any) -> list[Any]:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        return value.tolist()
