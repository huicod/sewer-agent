"""Lazy, offline Hugging Face adapters for bounded open-detector experiments."""

from __future__ import annotations

import gc
import importlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipe_inspector.contracts.open_scan import (
    OPEN_VOCABULARY_VERSION,
    OpenCandidate,
    OpenDetectionBatch,
)
from pipe_inspector.vision.open_detector import OPEN_VOCABULARY


@dataclass(frozen=True)
class ModelSpec:
    repo_id: str
    revision: str
    confidence: float
    assets: tuple[str, ...]


MODEL_SPECS: dict[str, ModelSpec] = {
    "mm-gdino-base": ModelSpec(
        repo_id="openmmlab-community/mm_grounding_dino_base_o365v1_goldg_v3det",
        revision="6f52a61753b22842954209e4b190b22135c57bf0",
        confidence=0.30,
        assets=(
            "model.safetensors",
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "vocab.txt",
        ),
    ),
    "owlv2-large": ModelSpec(
        repo_id="google/owlv2-large-patch14-ensemble",
        revision="95e26936e865f87db1742128404b3c035d47d89d",
        confidence=0.10,
        assets=(
            "model.safetensors",
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "added_tokens.json",
            "vocab.json",
            "merges.txt",
        ),
    ),
}


class HFOpenDetector:
    """Local-only adapter for one pinned Hugging Face detector checkpoint."""

    def __init__(
        self,
        checkpoint: Path,
        *,
        model_key: str,
        device: str = "cuda:0",
    ) -> None:
        try:
            spec = MODEL_SPECS[model_key]
        except KeyError as exc:
            raise ValueError(f"unsupported model_key: {model_key!r}") from exc

        self.checkpoint = Path(checkpoint)
        self.model_key = model_key
        self.device = device
        self.confidence = spec.confidence
        self.detector_id = model_key
        self.model_version = spec.revision
        self.vocabulary_version = OPEN_VOCABULARY_VERSION
        self.precision = (
            "float32"
            if model_key == "mm-gdino-base" or not device.startswith("cuda")
            else "float16"
        )
        self._processor: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None

    def predict_image(self, path: Path) -> OpenDetectionBatch:
        image_path = Path(path)
        if not image_path.is_file():
            raise FileNotFoundError(f"input image does not exist: {image_path}")
        self._ensure_loaded()

        from PIL import Image

        with Image.open(image_path) as original:
            image = original.convert("RGB")
            width, height = image.size
            candidates = self._predict_candidates(image, width=width, height=height)

        return OpenDetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            vocabulary_version=self.vocabulary_version,
            width=width,
            height=height,
            candidates=tuple(candidates),
        )

    def unload(self) -> None:
        model = self._model
        torch = self._torch
        self._model = None
        self._processor = None
        self._torch = None
        if model is None:
            return
        try:
            model.cpu()
        finally:
            del model
            gc.collect()
            if torch is not None and self.device.startswith("cuda"):
                torch.cuda.empty_cache()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        self._validate_checkpoint()

        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        spec = MODEL_SPECS[self.model_key]
        common = {
            "revision": spec.revision,
            "local_files_only": True,
            "trust_remote_code": False,
        }
        processor = transformers.AutoProcessor.from_pretrained(
            str(self.checkpoint), **common, use_fast=self.model_key == "mm-gdino-base"
        )
        config = transformers.AutoConfig.from_pretrained(str(self.checkpoint), **common)
        expected_type = "mm-grounding-dino" if self.model_key == "mm-gdino-base" else "owlv2"
        if getattr(config, "model_type", None) != expected_type:
            raise ValueError(
                f"checkpoint model_type must be {expected_type!r}, got "
                f"{getattr(config, 'model_type', None)!r}"
            )
        if self.model_key == "mm-gdino-base":
            config.disable_custom_kernels = True

        dtype = getattr(torch, self.precision)
        model_kwargs = common | {
            "config": config,
            "use_safetensors": True,
            "dtype": dtype,
        }
        model = transformers.AutoModelForZeroShotObjectDetection.from_pretrained(
            str(self.checkpoint), **model_kwargs
        )
        model.to(self.device)
        model.eval()
        self._processor = processor
        self._model = model
        self._torch = torch

    def _predict_candidates(self, image: Any, *, width: int, height: int) -> list[OpenCandidate]:
        assert self._processor is not None
        assert self._model is not None
        assert self._torch is not None
        names = [name for name, _label in OPEN_VOCABULARY]

        if self.model_key == "mm-gdino-base":
            caption = ". ".join(names) + "."
            inputs = self._processor(
                images=image,
                text=caption,
                return_tensors="pt",
                return_offsets_mapping=True,
            )
            offsets = inputs.pop("offset_mapping")
        else:
            inputs = self._processor(images=image, text=[names], return_tensors="pt")
            offsets = None
        dtype = getattr(self._torch, self.precision)
        inputs.to(device=self.device, dtype=dtype)

        with self._torch.inference_mode():
            outputs = self._model(**inputs)

        if self.model_key == "mm-gdino-base":
            return self._postprocess_mm(outputs, offsets, width=width, height=height)
        results = self._processor.post_process_grounded_object_detection(
            outputs=outputs,
            threshold=self.confidence,
            target_sizes=[(height, width)],
        )
        if len(results) != 1:
            raise ValueError(f"open detector returned {len(results)} results for one image")
        return self._postprocess_owl(results[0], width=width, height=height)

    def _validate_checkpoint(self) -> None:
        if not self.checkpoint.is_dir():
            raise FileNotFoundError(f"Hugging Face checkpoint does not exist: {self.checkpoint}")
        spec = MODEL_SPECS[self.model_key]
        metadata_root = self.checkpoint / ".cache" / "huggingface" / "download"
        setup = (
            f"provision {spec.repo_id}@{spec.revision} with "
            "huggingface_hub.snapshot_download"
        )
        for filename in spec.assets:
            asset = self.checkpoint / filename
            metadata = metadata_root / f"{filename}.metadata"
            if not asset.is_file():
                raise FileNotFoundError(f"checkpoint asset {filename} is missing; {setup}")
            if not metadata.is_file():
                raise FileNotFoundError(
                    f"Hugging Face download metadata for {filename} is missing; {setup}"
                )
            with metadata.open(encoding="utf-8") as handle:
                downloaded_revision = handle.readline().strip()
            if downloaded_revision != spec.revision:
                raise ValueError(
                    f"checkpoint revision for {filename} is {downloaded_revision!r}, "
                    f"expected {spec.revision!r}; {setup}"
                )

    def _postprocess_mm(
        self, outputs: Any, offsets: Any, *, width: int, height: int
    ) -> list[OpenCandidate]:
        batch_logits = self._tensor_values(outputs.logits)
        batch_boxes = self._tensor_values(outputs.pred_boxes)
        offset_batches = self._tensor_values(offsets)
        if len(batch_logits) != 1 or len(batch_boxes) != 1 or len(offset_batches) != 1:
            raise ValueError("open detector returned an invalid batch size")
        logits = batch_logits[0]
        boxes = batch_boxes[0]
        if len(logits) != len(boxes):
            raise ValueError("open detector box and logit arrays differ in length")
        if not logits:
            return []

        token_groups = self._caption_token_groups(offset_batches[0])
        detections: list[tuple[int, float, tuple[float, float, float, float]]] = []
        for query_logits, raw_box in zip(logits, boxes, strict=True):
            class_scores: list[float] = []
            for token_ids in token_groups:
                try:
                    token_logits = [float(query_logits[token_id]) for token_id in token_ids]
                except (IndexError, TypeError, ValueError) as exc:
                    raise ValueError("MM-Grounding-DINO returned malformed token logits") from exc
                if not all(math.isfinite(value) for value in token_logits):
                    raise ValueError("open detector returned a nonfinite score")
                class_scores.append(
                    sum(self._sigmoid(value) for value in token_logits) / len(token_logits)
                )

            class_id, score = max(enumerate(class_scores), key=lambda item: item[1])
            if score <= self.confidence:
                continue
            try:
                center_x, center_y, box_width, box_height = (
                    float(value) for value in raw_box
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"open detector returned malformed bbox: {raw_box!r}") from exc
            detections.append(
                (
                    class_id,
                    score,
                    (
                        (center_x - box_width / 2.0) * width,
                        (center_y - box_height / 2.0) * height,
                        (center_x + box_width / 2.0) * width,
                        (center_y + box_height / 2.0) * height,
                    ),
                )
            )
        return self._finalize(detections, width=width, height=height)

    def _postprocess_owl(
        self, result: dict[str, Any], *, width: int, height: int
    ) -> list[OpenCandidate]:
        try:
            scores = self._tensor_values(result["scores"])
            labels = self._tensor_values(result["labels"])
            boxes = self._tensor_values(result["boxes"])
        except KeyError as exc:
            raise ValueError(f"open detector result is missing {exc.args[0]!r}") from exc
        if not (len(scores) == len(labels) == len(boxes)):
            raise ValueError("open detector box, score, and class arrays differ in length")

        detections: list[tuple[int, float, tuple[float, float, float, float]]] = []
        for raw_score, raw_class_id, raw_box in zip(scores, labels, boxes, strict=True):
            try:
                class_number = float(raw_class_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid open detector class id: {raw_class_id!r}") from exc
            if not math.isfinite(class_number) or not class_number.is_integer():
                raise ValueError(f"invalid open detector class id: {raw_class_id!r}")
            try:
                coordinates = tuple(float(value) for value in raw_box)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"open detector returned malformed bbox: {raw_box!r}") from exc
            detections.append((int(class_number), float(raw_score), coordinates))
        return self._finalize(detections, width=width, height=height)

    @staticmethod
    def _caption_token_groups(offsets: list[Any]) -> list[list[int]]:
        spans: list[tuple[int, int]] = []
        cursor = 0
        for name, _label in OPEN_VOCABULARY:
            spans.append((cursor, cursor + len(name)))
            cursor += len(name) + 2

        groups: list[list[int]] = []
        for span_start, span_end in spans:
            token_ids: list[int] = []
            for token_id, raw_offset in enumerate(offsets):
                try:
                    token_start, token_end = (int(value) for value in raw_offset)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"MM-Grounding-DINO returned malformed token offset: {raw_offset!r}"
                    ) from exc
                if token_end > token_start and token_start < span_end and token_end > span_start:
                    token_ids.append(token_id)
            if not token_ids:
                raise ValueError(
                    "MM-Grounding-DINO tokenizer did not preserve a configured caption span"
                )
            groups.append(token_ids)
        return groups

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0.0:
            return 1.0 / (1.0 + math.exp(-value))
        exponent = math.exp(value)
        return exponent / (1.0 + exponent)

    @staticmethod
    def _finalize(
        detections: list[tuple[int, float, tuple[float, ...]]],
        *,
        width: int,
        height: int,
    ) -> list[OpenCandidate]:
        valid: list[tuple[int, float, tuple[float, float, float, float], int]] = []
        for order, (class_id, raw_score, raw_box) in enumerate(detections):
            if not 0 <= class_id < len(OPEN_VOCABULARY):
                raise ValueError(f"open detector returned unknown class id: {class_id}")
            score = float(raw_score)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"open detector returned invalid score: {raw_score!r}")
            if len(raw_box) != 4:
                raise ValueError(f"open detector returned malformed bbox: {raw_box!r}")
            if not all(math.isfinite(value) for value in raw_box):
                raise ValueError(f"open detector returned invalid bbox: {raw_box!r}")
            x0, y0, x1, y1 = raw_box
            clipped = (
                min(max(x0, 0.0), float(width)),
                min(max(y0, 0.0), float(height)),
                min(max(x1, 0.0), float(width)),
                min(max(y1, 0.0), float(height)),
            )
            if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
                continue
            valid.append((class_id, score, clipped, order))

        kept: list[tuple[int, float, tuple[float, float, float, float], int]] = []
        for detection in sorted(valid, key=lambda item: (-item[1], item[3])):
            class_id, _score, box, _order = detection
            if any(
                kept_class == class_id and HFOpenDetector._iou(box, kept_box) > 0.5
                for kept_class, _kept_score, kept_box, _kept_order in kept
            ):
                continue
            kept.append(detection)
            if len(kept) == 100:
                break

        candidates: list[OpenCandidate] = []
        for class_id, score, box, _order in kept:
            name, label = OPEN_VOCABULARY[class_id]
            candidates.append(OpenCandidate(label=label, name=name, score=score, bbox_xyxy=box))
        return candidates

    @staticmethod
    def _iou(
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
        intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
        intersection = intersection_width * intersection_height
        left_area = (left[2] - left[0]) * (left[3] - left[1])
        right_area = (right[2] - right[0]) * (right[3] - right[1])
        return intersection / (left_area + right_area - intersection)

    @staticmethod
    def _tensor_values(value: Any) -> Any:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "tolist"):
            value = value.tolist()
        return value
