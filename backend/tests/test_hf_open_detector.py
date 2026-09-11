from __future__ import annotations

import importlib
import math
import types
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.vision.open_detector import OPEN_VOCABULARY

EXPECTED_VOCABULARY = (
    ("lateral connection opening in sewer pipe", DetectionLabel.AJ),
    ("pipe deformation", DetectionLabel.BX),
    ("pipe corrosion", DetectionLabel.FS),
    ("crack in sewer pipe", DetectionLabel.PL),
    ("pipe blockage", DetectionLabel.BLOCK),
    ("root intrusion", "OTHER"),
    ("water infiltration", "OTHER"),
)

MODEL_REVISIONS = {
    "mm-gdino-base": "6f52a61753b22842954209e4b190b22135c57bf0",
    "owlv2-large": "95e26936e865f87db1742128404b3c035d47d89d",
}
MODEL_ASSETS = {
    "mm-gdino-base": (
        "model.safetensors",
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
    ),
    "owlv2-large": (
        "model.safetensors",
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "merges.txt",
    ),
}


class FakeInputs(dict):
    def __init__(
        self,
        *args: Any,
        moves: list[tuple[tuple[Any, ...], dict[str, Any]]],
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.moves = moves

    def to(self, *args: Any, **kwargs: Any) -> FakeInputs:
        self.moves.append((args, kwargs))
        return self


class FakeModel:
    def __init__(self, *, model_key: str) -> None:
        self.model_key = model_key
        self.to_calls: list[str] = []
        self.eval_calls = 0
        self.cpu_calls = 0

    def to(self, device: str) -> FakeModel:
        self.to_calls.append(device)
        return self

    def eval(self) -> FakeModel:
        self.eval_calls += 1
        return self

    def cpu(self) -> FakeModel:
        self.cpu_calls += 1
        return self

    def __call__(self, **inputs: Any) -> object:
        if self.model_key == "mm-gdino-base":
            return types.SimpleNamespace(logits=[[]], pred_boxes=[[]])
        return types.SimpleNamespace()


class FakeProcessor:
    def __init__(self, *, model_key: str, calls: list[tuple[str, Any]]) -> None:
        self.model_key = model_key
        self.calls = calls
        self.last_inputs: FakeInputs | None = None

    def __call__(self, **kwargs: Any) -> FakeInputs:
        self.calls.append(("preprocess", kwargs))
        values: dict[str, Any] = {"pixel_values": "pixels"}
        if self.model_key == "mm-gdino-base":
            values["offset_mapping"] = [[(0, 0)]]
        self.last_inputs = FakeInputs(values, moves=[])
        return self.last_inputs

    def post_process_grounded_object_detection(self, **kwargs: Any) -> list[dict[str, list[Any]]]:
        self.calls.append(("postprocess", kwargs))
        return [{"scores": [], "labels": [], "boxes": []}]


def install_fake_hf_vendor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_key: str,
) -> tuple[list[tuple[str, Any]], FakeModel, object, FakeProcessor]:
    calls: list[tuple[str, Any]] = []
    model = FakeModel(model_key=model_key)
    processor = FakeProcessor(model_key=model_key, calls=calls)
    expected_model_type = "mm-grounding-dino" if model_key == "mm-gdino-base" else "owlv2"
    config = types.SimpleNamespace(model_type=expected_model_type, disable_custom_kernels=False)

    class Loader:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> object:
            calls.append(("processor_load", (path, kwargs)))
            return processor

    class ConfigLoader:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> object:
            calls.append(("config_load", (path, kwargs)))
            return config

    class ModelLoader:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> FakeModel:
            calls.append(("model_load", (path, kwargs.copy())))
            return model

    fake_transformers = types.SimpleNamespace(
        AutoProcessor=Loader,
        AutoConfig=ConfigLoader,
        AutoModelForZeroShotObjectDetection=ModelLoader,
    )
    fake_torch = types.SimpleNamespace(
        float16="float16",
        float32="float32",
        inference_mode=nullcontext,
        cuda=types.SimpleNamespace(empty_cache=lambda: calls.append(("empty_cache", None))),
    )

    def fake_import(name: str) -> object:
        calls.append(("import", name))
        return {"torch": fake_torch, "transformers": fake_transformers}[name]

    monkeypatch.setattr(importlib, "import_module", fake_import)
    return calls, model, config, processor


def local_image(tmp_path: Path, *, extensionless: bool = False) -> Path:
    from PIL import Image

    image = tmp_path / ("source" if extensionless else "frame.jpg")
    Image.new("RGBA", (200, 100), color=(12, 34, 56, 78)).save(image, format="PNG")
    return image


def local_checkpoint(
    tmp_path: Path,
    *,
    model_key: str,
    revision: str | None = None,
) -> Path:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    metadata = checkpoint / ".cache" / "huggingface" / "download"
    metadata.mkdir(parents=True)
    pinned_revision = revision or MODEL_REVISIONS[model_key]
    for filename in MODEL_ASSETS[model_key]:
        (checkpoint / filename).write_text("fixture")
        (metadata / f"{filename}.metadata").write_text(f"{pinned_revision}\nfixture-etag\n")
    return checkpoint


def inject_loaded_vendor(detector: Any, *, processor: Any, model: Any) -> None:
    detector._processor = processor
    detector._model = model
    detector._torch = types.SimpleNamespace(
        float16="float16", float32="float32", inference_mode=nullcontext
    )


def as_logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))


def empty_output(**inputs: Any) -> object:
    return types.SimpleNamespace()


def test_constructor_is_lazy_and_exposes_pinned_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipe_inspector.vision.hf_open_detector import MODEL_SPECS, HFOpenDetector

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )
    detector = HFOpenDetector(Path("/not/read/model"), model_key="mm-gdino-base")

    assert detector.detector_id == "mm-gdino-base"
    assert detector.model_version == "6f52a61753b22842954209e4b190b22135c57bf0"
    assert detector.precision == "float32"
    assert detector.vocabulary_version == "sewer-open-v1"
    assert detector.confidence == pytest.approx(0.30)
    assert MODEL_SPECS["mm-gdino-base"].repo_id == (
        "openmmlab-community/mm_grounding_dino_base_o365v1_goldg_v3det"
    )


def test_constructor_rejects_unknown_model_key() -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    with pytest.raises(ValueError, match="model_key"):
        HFOpenDetector(Path("model"), model_key="unknown")


@pytest.mark.parametrize(
    ("model_key", "device", "dtype", "extra_model_kwargs"),
    [
        ("mm-gdino-base", "cuda:0", "float32", {}),
        ("mm-gdino-base", "cpu", "float32", {}),
        ("owlv2-large", "cuda:0", "float16", {}),
        ("owlv2-large", "cpu", "float32", {}),
    ],
)
def test_prediction_loads_only_local_pinned_safetensors_with_native_processor(
    model_key: str,
    device: str,
    dtype: str,
    extra_model_kwargs: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipe_inspector.vision.hf_open_detector import MODEL_SPECS, HFOpenDetector

    checkpoint = local_checkpoint(tmp_path, model_key=model_key)
    image = local_image(tmp_path)
    calls, model, config, processor = install_fake_hf_vendor(monkeypatch, model_key=model_key)

    detector = HFOpenDetector(checkpoint, model_key=model_key, device=device)
    batch = detector.predict_image(image)

    common = {
        "revision": MODEL_SPECS[model_key].revision,
        "local_files_only": True,
        "trust_remote_code": False,
    }
    assert (
        "processor_load",
        (str(checkpoint), common | {"use_fast": model_key == "mm-gdino-base"}),
    ) in calls
    assert ("config_load", (str(checkpoint), common)) in calls
    assert (
        "model_load",
        (
            str(checkpoint),
            common
            | {"config": config, "use_safetensors": True, "dtype": dtype}
            | extra_model_kwargs,
        ),
    ) in calls
    assert config.disable_custom_kernels is (model_key == "mm-gdino-base")
    assert detector.precision == dtype
    assert model.to_calls == [device]
    assert model.eval_calls == 1
    assert processor.last_inputs is not None
    assert processor.last_inputs.moves == [((), {"device": device, "dtype": dtype})]
    assert batch.candidates == ()


def test_prediction_always_decodes_original_as_pil_rgb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    checkpoint = local_checkpoint(tmp_path, model_key="owlv2-large")
    image = local_image(tmp_path, extensionless=True)
    calls, _, _, _ = install_fake_hf_vendor(monkeypatch, model_key="owlv2-large")

    batch = HFOpenDetector(checkpoint, model_key="owlv2-large", device="cpu").predict_image(image)

    preprocess = next(value for name, value in calls if name == "preprocess")
    source = preprocess["images"]
    assert isinstance(source, Image.Image)
    assert source.mode == "RGB"
    assert source.size == (200, 100)
    assert source.getpixel((0, 0)) == (12, 34, 56)
    assert preprocess["text"] == [[name for name, _ in OPEN_VOCABULARY]]
    assert (batch.width, batch.height) == (200, 100)


def test_mm_maps_known_caption_spans_with_mean_token_probability(
    tmp_path: Path,
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)
    calls: list[dict[str, Any]] = []
    offsets = [
        (0, 0),
        (0, 7),
        (8, 40),
        (42, 58),
        (60, 74),
        (76, 95),
        (97, 110),
        (112, 126),
        (128, 146),
        (0, 0),
    ]

    class Processor:
        def __call__(self, **kwargs: Any) -> FakeInputs:
            calls.append(kwargs)
            return FakeInputs(
                {"pixel_values": "pixels", "offset_mapping": [offsets]}, moves=[]
            )

    low = as_logit(0.01)
    logits = [
        [low, as_logit(0.20), as_logit(0.60), low, low, low, low, low, low, low],
        [low, low, low, low, low, low, low, as_logit(0.90), low, low],
    ]
    def model(**kwargs: Any) -> object:
        return types.SimpleNamespace(
            logits=[logits],
            pred_boxes=[[[0.5, 0.5, 0.5, 0.5], [0.1, 0.1, 0.4, 0.4]]],
        )
    detector = HFOpenDetector(Path("unused"), model_key="mm-gdino-base", device="cpu")
    inject_loaded_vendor(detector, processor=Processor(), model=model)

    batch = detector.predict_image(image)

    assert len(calls) == 1
    assert calls[0]["text"] == ". ".join(name for name, _ in EXPECTED_VOCABULARY) + "."
    assert calls[0]["return_tensors"] == "pt"
    assert calls[0]["return_offsets_mapping"] is True
    assert [(candidate.name, candidate.label) for candidate in batch.candidates] == [
        ("root intrusion", "OTHER"),
        ("lateral connection opening in sewer pipe", DetectionLabel.AJ),
    ]
    assert [candidate.score for candidate in batch.candidates] == pytest.approx([0.90, 0.40])
    assert [candidate.bbox_xyxy for candidate in batch.candidates] == [
        (0.0, 0.0, 60.00000000000001, 30.000000000000004),
        (50.0, 25.0, 150.0, 75.0),
    ]


def test_owl_uses_original_target_size_and_maps_query_indices(tmp_path: Path) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)
    postprocess_calls: list[dict[str, Any]] = []

    class Processor:
        def __call__(self, **kwargs: Any) -> FakeInputs:
            return FakeInputs({"pixel_values": "pixels"}, moves=[])

        def post_process_grounded_object_detection(self, **kwargs: Any) -> list[dict[str, Any]]:
            postprocess_calls.append(kwargs)
            height, width = kwargs["target_sizes"][0]
            return [
                {
                    "scores": [0.85, 0.75],
                    "labels": [6, 1],
                    "boxes": [
                        [width * 0.1, height * 0.1, width * 0.9, height * 0.9],
                        [-0.01, -0.02, width + 0.01, height + 0.02],
                    ],
                }
            ]

    detector = HFOpenDetector(Path("unused"), model_key="owlv2-large", device="cpu")
    inject_loaded_vendor(detector, processor=Processor(), model=empty_output)

    batch = detector.predict_image(image)

    assert postprocess_calls[0]["target_sizes"] == [(100, 200)]
    assert postprocess_calls[0]["threshold"] == pytest.approx(0.10)
    assert [(candidate.name, candidate.label) for candidate in batch.candidates] == [
        ("water infiltration", "OTHER"),
        ("pipe deformation", DetectionLabel.BX),
    ]
    assert [candidate.bbox_xyxy for candidate in batch.candidates] == [
        (20.0, 10.0, 180.0, 90.0),
        (0.0, 0.0, 200.0, 100.0),
    ]


def test_same_concept_nms_is_score_ordered_stable_and_keeps_other_concepts(
    tmp_path: Path,
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)

    class Processor:
        def __call__(self, **kwargs: Any) -> FakeInputs:
            return FakeInputs({"pixel_values": "pixels"}, moves=[])

        def post_process_grounded_object_detection(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "scores": [0.8, 0.9, 0.9, 0.7],
                    "labels": [0, 0, 0, 1],
                    "boxes": [
                        [10.0, 10.0, 90.0, 90.0],
                        [0.0, 0.0, 100.0, 100.0],
                        [120.0, 0.0, 180.0, 60.0],
                        [10.0, 10.0, 90.0, 90.0],
                    ],
                }
            ]

    detector = HFOpenDetector(Path("unused"), model_key="owlv2-large", device="cpu")
    inject_loaded_vendor(detector, processor=Processor(), model=empty_output)

    batch = detector.predict_image(image)

    assert [(candidate.score, candidate.name) for candidate in batch.candidates] == [
        (0.9, "lateral connection opening in sewer pipe"),
        (0.9, "lateral connection opening in sewer pipe"),
        (0.7, "pipe deformation"),
    ]
    assert [candidate.bbox_xyxy for candidate in batch.candidates[:2]] == [
        (0.0, 0.0, 100.0, 100.0),
        (120.0, 0.0, 180.0, 60.0),
    ]


@pytest.mark.parametrize(
    ("scores", "labels", "boxes", "message"),
    [
        ([math.nan], [0], [[0.0, 0.0, 1.0, 1.0]], "score"),
        ([0.5], [0], [[0.0, 0.0, math.inf, 1.0]], "bbox"),
        ([0.5], [7], [[0.0, 0.0, 1.0, 1.0]], "class id"),
        ([0.5], [], [[0.0, 0.0, 1.0, 1.0]], "differ in length"),
    ],
)
def test_prediction_rejects_invalid_vendor_output(
    scores: list[float],
    labels: list[int],
    boxes: list[list[float]],
    message: str,
    tmp_path: Path,
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)

    class Processor:
        def __call__(self, **kwargs: Any) -> FakeInputs:
            return FakeInputs({"pixel_values": "pixels"}, moves=[])

        def post_process_grounded_object_detection(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"scores": scores, "labels": labels, "boxes": boxes}]

    detector = HFOpenDetector(Path("unused"), model_key="owlv2-large", device="cpu")
    inject_loaded_vendor(detector, processor=Processor(), model=empty_output)

    with pytest.raises(ValueError, match=message):
        detector.predict_image(image)


def test_degenerate_clipped_boxes_are_dropped_and_result_is_capped_at_100(
    tmp_path: Path,
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)
    boxes = [[-10.0, 1.0, -1.0, 2.0]]
    boxes.extend([[float(index), 1.0, index + 0.5, 2.0] for index in range(101)])

    class Processor:
        def __call__(self, **kwargs: Any) -> FakeInputs:
            return FakeInputs({"pixel_values": "pixels"}, moves=[])

        def post_process_grounded_object_detection(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "scores": [0.99] + [0.98 - index * 0.001 for index in range(101)],
                    "labels": [0] * 102,
                    "boxes": boxes,
                }
            ]

    detector = HFOpenDetector(Path("unused"), model_key="owlv2-large", device="cpu")
    inject_loaded_vendor(detector, processor=Processor(), model=empty_output)

    batch = detector.predict_image(image)

    assert len(batch.candidates) == 100
    assert all(candidate.bbox_xyxy[0] < candidate.bbox_xyxy[2] for candidate in batch.candidates)


def test_missing_checkpoint_fails_before_importing_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    image = local_image(tmp_path)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    with pytest.raises(FileNotFoundError, match="checkpoint"):
        HFOpenDetector(tmp_path / "missing", model_key="owlv2-large").predict_image(image)


def test_revision_metadata_mismatch_fails_before_importing_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    checkpoint = local_checkpoint(tmp_path, model_key="mm-gdino-base", revision="wrong-revision")
    image = local_image(tmp_path)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    with pytest.raises(ValueError, match="revision.*snapshot_download"):
        HFOpenDetector(checkpoint, model_key="mm-gdino-base").predict_image(image)


def test_missing_family_vocab_asset_fails_before_importing_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    checkpoint = local_checkpoint(tmp_path, model_key="owlv2-large")
    (checkpoint / "merges.txt").unlink()
    image = local_image(tmp_path)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    with pytest.raises(FileNotFoundError, match="merges.txt.*snapshot_download"):
        HFOpenDetector(checkpoint, model_key="owlv2-large").predict_image(image)


def test_unload_releases_gpu_model_is_idempotent_and_allows_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    checkpoint = local_checkpoint(tmp_path, model_key="owlv2-large")
    image = local_image(tmp_path)
    calls, model, _, _ = install_fake_hf_vendor(monkeypatch, model_key="owlv2-large")
    detector = HFOpenDetector(checkpoint, model_key="owlv2-large")

    detector.unload()
    detector.predict_image(image)
    detector.unload()
    detector.unload()

    assert model.cpu_calls == 1
    assert calls.count(("empty_cache", None)) == 1
    assert detector._model is detector._processor is detector._torch is None

    detector.predict_image(image)
    assert sum(name == "model_load" for name, _value in calls) == 2
