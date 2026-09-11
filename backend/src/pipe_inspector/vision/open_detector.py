from __future__ import annotations

import gc
import importlib
import math
from pathlib import Path
from typing import Any, Protocol

from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.open_scan import (
    OPEN_VOCABULARY_VERSION,
    OpenCandidate,
    OpenDetectionBatch,
)

DETECTOR_ID = "yoloe"
CLIP_REQUIREMENT = "git+https://github.com/ultralytics/CLIP.git"

# One prompt per output concept. Ordinary objects and aliases are intentionally excluded.
OPEN_VOCABULARY: tuple[tuple[str, DetectionLabel | str], ...] = (
    ("lateral connection opening in sewer pipe", DetectionLabel.AJ),
    ("pipe deformation", DetectionLabel.BX),
    ("pipe corrosion", DetectionLabel.FS),
    ("crack in sewer pipe", DetectionLabel.PL),
    ("pipe blockage", DetectionLabel.BLOCK),
    ("root intrusion", "OTHER"),
    ("water infiltration", "OTHER"),
)


class OpenDetectorAdapter(Protocol):
    detector_id: str
    model_version: str
    vocabulary_version: str

    def predict_image(self, path: Path) -> OpenDetectionBatch: ...

    def unload(self) -> None: ...


class YOLOEOpenDetector:
    """Lazy local-only adapter for fixed-vocabulary YOLOE candidate detection."""

    def __init__(
        self,
        checkpoint: Path,
        text_encoder_path: Path,
        *,
        device: str = "0",
        image_size: int = 640,
        confidence: float = 0.25,
    ) -> None:
        if image_size <= 0:
            raise ValueError("image_size must be positive")
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")

        self.checkpoint = Path(checkpoint)
        self.text_encoder_path = Path(text_encoder_path)
        self.device = device
        self.image_size = image_size
        self.confidence = confidence
        self.detector_id = DETECTOR_ID
        self.model_version = self.checkpoint.name
        self.vocabulary_version = OPEN_VOCABULARY_VERSION
        self._model: Any | None = None

    def predict_image(self, path: Path) -> OpenDetectionBatch:
        image_path = Path(path)
        if not image_path.is_file():
            raise FileNotFoundError(f"input image does not exist: {image_path}")

        source: Any = str(image_path)
        if not image_path.suffix:
            # MediaStorage uses extensionless `source` files; the vendor path loader
            # filters by suffix. Decode the original pixels without re-encoding a file.
            from PIL import Image

            with Image.open(image_path) as original:
                source = original.convert("RGB")
        model = self._ensure_model()
        results = tuple(
            model.predict(
                source=source,
                device=self.device,
                imgsz=self.image_size,
                conf=self.confidence,
                max_det=100,
                verbose=False,
            )
        )
        if len(results) != 1:
            raise RuntimeError(f"open detector returned {len(results)} results for one image")
        return self._translate_result(results[0])

    def unload(self) -> None:
        model = self._model
        self._model = None
        if model is None:
            return

        model.predictor = None
        underlying = getattr(model, "model", None)
        if underlying is not None and hasattr(underlying, "clip_model"):
            del underlying.clip_model
        try:
            model.cpu()
        finally:
            del model
            gc.collect()
            torch = importlib.import_module("torch")
            if hasattr(torch, "cuda"):
                torch.cuda.empty_cache()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"YOLOE checkpoint does not exist: {self.checkpoint}")
        if not self.text_encoder_path.is_file():
            raise FileNotFoundError(
                f"YOLOE text encoder does not exist: {self.text_encoder_path}"
            )
        if importlib.util.find_spec("clip") is None:
            raise ImportError(
                "YOLOE text prompting requires the local CLIP tokenizer package; "
                f"install it during deployment setup with: pip install {CLIP_REQUIREMENT}"
            )
        try:
            importlib.import_module("clip")
        except ImportError as exc:
            raise ImportError(
                "YOLOE text prompting requires a working local CLIP tokenizer package; "
                f"reinstall it during deployment setup with: pip install {CLIP_REQUIREMENT}"
            ) from exc

        ultralytics = importlib.import_module("ultralytics")
        model = ultralytics.YOLOE(str(self.checkpoint), task="segment")
        text_model = importlib.import_module("ultralytics.nn.text_model")
        encoder = text_model.MobileCLIPTS(device="cpu", weight=str(self.text_encoder_path))
        names = [name for name, _label in OPEN_VOCABULARY]

        model.model.clip_model = encoder
        try:
            embeddings = model.model.get_text_pe(names, cache_clip_model=True)
            model.set_classes(names, embeddings)
        finally:
            del model.model.clip_model

        self._model = model
        return model

    def _translate_result(self, result: Any) -> OpenDetectionBatch:
        cpu_result = result.cpu()
        try:
            height, width = (int(value) for value in cpu_result.orig_shape)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"open detector returned invalid original shape: {cpu_result.orig_shape!r}"
            ) from exc

        candidates: list[OpenCandidate] = []
        boxes = cpu_result.boxes
        if boxes is not None:
            xyxy = self._tensor_values(boxes.xyxy)
            scores = self._tensor_values(boxes.conf)
            class_ids = self._tensor_values(boxes.cls)
            if not (len(xyxy) == len(scores) == len(class_ids)):
                raise ValueError("open detector box, score, and class arrays differ in length")

            for coordinates, score, raw_class_id in zip(
                xyxy, scores, class_ids, strict=True
            ):
                class_number = float(raw_class_id)
                if not math.isfinite(class_number) or not class_number.is_integer():
                    raise ValueError(f"invalid open detector class id: {raw_class_id!r}")
                class_id = int(class_number)
                if not 0 <= class_id < len(OPEN_VOCABULARY):
                    raise ValueError(f"open detector returned unknown class id: {class_id}")
                name, label = OPEN_VOCABULARY[class_id]
                coordinate_values = tuple(float(value) for value in coordinates)
                if len(coordinate_values) != 4:
                    raise ValueError(
                        f"open detector returned malformed bbox: {coordinate_values!r}"
                    )
                candidates.append(
                    OpenCandidate(
                        label=label,
                        name=name,
                        score=float(score),
                        bbox_xyxy=coordinate_values,
                    )
                )

        return OpenDetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            vocabulary_version=self.vocabulary_version,
            width=width,
            height=height,
            candidates=tuple(candidates),
        )

    @staticmethod
    def _tensor_values(value: Any) -> list[Any]:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        return value.tolist()
