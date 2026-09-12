from __future__ import annotations

import importlib
import json
import math
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from PIL import Image
from pipe_inspector.contracts.common import ImageRegion

SAM_REVISION = "665f8e2ad61cf5f53d65644ff27c8ee525124610"
DEPTH_REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
SAM_ASSETS = (
    "model.safetensors",
    "config.json",
    "preprocessor_config.json",
    "processor_config.json",
    "video_preprocessor_config.json",
)
DEPTH_ASSETS = ("model.safetensors", "config.json", "preprocessor_config.json")


def _module():
    return importlib.import_module("pipe_inspector.vision.geometry_models")


def _snapshot(tmp_path: Path, name: str, assets: tuple[str, ...], revision: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    for filename in assets:
        (root / filename).touch()
    repo_id = (
        "facebook/sam2.1-hiera-large"
        if revision == SAM_REVISION
        else "depth-anything/Depth-Anything-V2-Small-hf"
    )
    (root / "provenance.json").write_text(
        json.dumps({"repo_id": repo_id, "revision": revision}),
        encoding="utf-8",
    )
    return root


class _Batch(dict[str, Any]):
    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.to_calls: list[dict[str, Any]] = []

    def to(self, **kwargs: Any):
        self.to_calls.append(kwargs)
        return self


class _Model:
    def __init__(self, output: Any, *, fail_transfer: bool = False) -> None:
        self.output = output
        self.fail_transfer = fail_transfer
        self.to_calls: list[str] = []
        self.eval_calls = 0
        self.cpu_calls = 0
        self.forward_calls: list[dict[str, Any]] = []

    def to(self, device: str):
        self.to_calls.append(device)
        if self.fail_transfer:
            raise RuntimeError("device transfer failed")
        return self

    def eval(self):
        self.eval_calls += 1
        return self

    def cpu(self):
        self.cpu_calls += 1
        return self

    def __call__(self, **kwargs: Any) -> Any:
        self.forward_calls.append(kwargs)
        return self.output


class _Factory:
    def __init__(
        self,
        value: Any,
        *,
        build: bool = False,
        loading_info: dict[str, Any] | None = None,
    ) -> None:
        self.value = value
        self.build = build
        self.loading_info = loading_info or {
            "missing_keys": [],
            "unexpected_keys": [],
            "mismatched_keys": [],
            "error_msgs": [],
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def from_pretrained(self, checkpoint: str, **kwargs: Any) -> Any:
        self.calls.append((checkpoint, kwargs))
        value = self.value() if self.build else self.value
        if kwargs.get("output_loading_info"):
            return value, self.loading_info
        return value


class _SamProcessor:
    def __init__(self, mask: torch.Tensor) -> None:
        self.mask = mask
        self.calls: list[dict[str, Any]] = []
        self.post_calls: list[tuple[Any, Any, dict[str, Any]]] = []
        self.last_batch: _Batch | None = None

    def __call__(self, **kwargs: Any) -> _Batch:
        self.calls.append(kwargs)
        self.last_batch = _Batch(
            pixel_values=torch.zeros((1, 3, 1024, 1024)),
            input_boxes=torch.tensor([[[1.0, 2.0, 3.0, 4.0]]]),
            original_sizes=torch.tensor([[5, 7]]),
        )
        return self.last_batch

    def post_process_masks(self, masks: Any, original_sizes: Any, **kwargs: Any) -> list[Any]:
        self.post_calls.append((masks, original_sizes, kwargs))
        return [self.mask]


class _DepthProcessor:
    def __init__(self, depth: torch.Tensor) -> None:
        self.depth = depth
        self.calls: list[dict[str, Any]] = []
        self.post_calls: list[tuple[Any, list[tuple[int, int]]]] = []
        self.last_batch: _Batch | None = None

    def __call__(self, **kwargs: Any) -> _Batch:
        self.calls.append(kwargs)
        self.last_batch = _Batch(pixel_values=torch.zeros((1, 3, 518, 728)))
        return self.last_batch

    def post_process_depth_estimation(
        self, outputs: Any, *, target_sizes: list[tuple[int, int]]
    ) -> list[dict[str, torch.Tensor]]:
        self.post_calls.append((outputs, target_sizes))
        return [{"predicted_depth": self.depth}]


def _fake_transformers(
    *,
    processor: Any,
    model: Any,
    model_type: str,
    segmenter: bool,
    loading_info: dict[str, Any] | None = None,
) -> tuple[Any, _Factory, _Factory, _Factory]:
    processor_factory = _Factory(processor)
    config = types.SimpleNamespace(model_type=model_type)
    config_factory = _Factory(config)
    model_factory = _Factory(
        model,
        build=not isinstance(model, _Model),
        loading_info=loading_info,
    )
    namespace = types.SimpleNamespace(AutoConfig=config_factory)
    if segmenter:
        namespace.AutoProcessor = processor_factory
        namespace.AutoModelForMaskGeneration = model_factory
    else:
        namespace.AutoImageProcessor = processor_factory
        namespace.AutoModelForDepthEstimation = model_factory
    return namespace, processor_factory, config_factory, model_factory


def _install_imports(monkeypatch: pytest.MonkeyPatch, fake_transformers: Any) -> None:
    module = _module()
    real_import = importlib.import_module

    def import_module(name: str) -> Any:
        if name == "torch":
            return torch
        if name == "transformers":
            return fake_transformers
        return real_import(name)

    monkeypatch.setattr(module.importlib, "import_module", import_module)


def test_constructors_are_io_free_and_pin_public_model_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda self: (_ for _ in ()).throw(AssertionError("constructor performed I/O")),
    )
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"constructor imported {name}")),
    )

    segmenter = module.LocalSegmenter(Path("/not/read/sam"), device="cpu")
    depth = module.LocalRelativeDepth(Path("/not/read/depth"), device="cpu")

    assert (segmenter.model_id, segmenter.model_version) == (
        "facebook/sam2.1-hiera-large",
        SAM_REVISION,
    )
    assert (depth.model_id, depth.model_version, depth.larger_is) == (
        "depth-anything/Depth-Anything-V2-Small-hf",
        DEPTH_REVISION,
        "nearer",
    )


def test_segmenter_uses_one_original_xyxy_box_and_restores_boolean_raster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, "sam", SAM_ASSETS, SAM_REVISION)
    restored = torch.zeros((1, 1, 5, 7), dtype=torch.bool)
    restored[0, 0, 0, 6] = True  # outside prompt: adapter must not crop the mask
    processor = _SamProcessor(restored)
    output = types.SimpleNamespace(
        pred_masks=torch.zeros((1, 1, 1, 256, 256)),
        iou_scores=torch.tensor([[[0.625]]]),
    )
    model = _Model(output)
    fake, processor_factory, config_factory, model_factory = _fake_transformers(
        processor=processor, model=model, model_type="sam2_video", segmenter=True
    )
    _install_imports(monkeypatch, fake)
    image = Image.new("RGB", (7, 5), (12, 34, 56))
    prompt = ImageRegion(width=7, height=5, bbox_xyxy=(1, 2, 4, 5))

    prediction = module.LocalSegmenter(checkpoint, device="cpu").segment_image(image, prompt)

    assert prediction.mask.dtype == np.bool_
    assert prediction.mask.shape == (5, 7)
    assert prediction.mask[0, 6]
    assert prediction.score == pytest.approx(0.625)
    assert processor.calls == [
        {
            "images": image,
            "input_boxes": [[[1.0, 2.0, 4.0, 5.0]]],
            "return_tensors": "pt",
        }
    ]
    assert processor.last_batch is not None
    assert processor.last_batch.to_calls == [{"device": "cpu", "dtype": torch.float32}]
    assert processor.post_calls[0][2] == {"mask_threshold": 0.0, "binarize": True}
    common = {
        "revision": SAM_REVISION,
        "local_files_only": True,
        "trust_remote_code": False,
    }
    assert processor_factory.calls == [(str(checkpoint), common)]
    assert config_factory.calls == [(str(checkpoint), common)]
    assert model_factory.calls == [
        (
            str(checkpoint),
            common
            | {
                "config": config_factory.value,
                "use_safetensors": True,
                "dtype": torch.float32,
                "output_loading_info": True,
            },
        )
    ]
    assert model.forward_calls[0]["multimask_output"] is False


def test_depth_uses_original_rgb_and_restores_float32_raster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, "depth", DEPTH_ASSETS, DEPTH_REVISION)
    restored = torch.arange(35, dtype=torch.float64).reshape(5, 7)
    processor = _DepthProcessor(restored)
    output = types.SimpleNamespace(predicted_depth=torch.zeros((1, 518, 728)))
    model = _Model(output)
    fake, processor_factory, config_factory, model_factory = _fake_transformers(
        processor=processor, model=model, model_type="depth_anything", segmenter=False
    )
    _install_imports(monkeypatch, fake)
    image = Image.new("RGB", (7, 5), (12, 34, 56))

    depth = module.LocalRelativeDepth(checkpoint, device="cpu").predict_depth(image)

    assert depth.dtype == np.float32
    assert depth.shape == (5, 7)
    assert depth[4, 6] == 34.0
    assert processor.calls == [{"images": image, "return_tensors": "pt"}]
    assert processor.post_calls == [(output, [(5, 7)])]
    assert processor.last_batch is not None
    assert processor.last_batch.to_calls == [{"device": "cpu", "dtype": torch.float32}]
    common = {
        "revision": DEPTH_REVISION,
        "local_files_only": True,
        "trust_remote_code": False,
    }
    assert processor_factory.calls == [(str(checkpoint), common | {"use_fast": False})]
    assert config_factory.calls == [(str(checkpoint), common)]
    assert model_factory.calls == [
        (
            str(checkpoint),
            common
            | {
                "config": config_factory.value,
                "use_safetensors": True,
                "dtype": torch.float32,
                "output_loading_info": True,
            },
        )
    ]


def test_extreme_depth_aspect_is_rejected_before_loading_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"loaded model dependency {name}")),
    )
    # Only ~6 MiB of pixels, but the pinned lower-bound resize projects to
    # 4,144 x 518 (>2M model pixels).
    image = Image.new("RGB", (4096, 512))

    with pytest.raises(ValueError, match="processed pixel limit"):
        module.LocalRelativeDepth(Path("/not/read/depth"), device="cpu").predict_depth(image)


@pytest.mark.parametrize(
    ("adapter_name", "assets", "revision", "missing"),
    [
        ("LocalSegmenter", SAM_ASSETS, SAM_REVISION, "processor_config.json"),
        ("LocalRelativeDepth", DEPTH_ASSETS, DEPTH_REVISION, "model.safetensors"),
    ],
)
def test_missing_pinned_asset_fails_before_importing_model_library(
    adapter_name: str,
    assets: tuple[str, ...],
    revision: str,
    missing: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, adapter_name, assets, revision)
    (checkpoint / missing).unlink()
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"imported {name}")),
    )
    adapter = getattr(module, adapter_name)(checkpoint, device="cpu")
    image = Image.new("RGB", (8, 6))

    with pytest.raises(FileNotFoundError, match=missing):
        if adapter_name == "LocalSegmenter":
            adapter.segment_image(
                image, ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4))
            )
        else:
            adapter.predict_depth(image)


def test_mismatched_download_revision_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, "depth", DEPTH_ASSETS, DEPTH_REVISION)
    (checkpoint / "provenance.json").write_text(
        json.dumps(
            {
                "repo_id": "depth-anything/Depth-Anything-V2-Small-hf",
                "revision": "wrong-revision",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"imported {name}")),
    )

    with pytest.raises(ValueError, match="wrong-revision"):
        module.LocalRelativeDepth(checkpoint, device="cpu").predict_depth(
            Image.new("RGB", (8, 6))
        )


@pytest.mark.parametrize("adapter_name", ["LocalSegmenter", "LocalRelativeDepth"])
def test_loader_rejects_non_strict_weight_mapping(
    adapter_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    is_segment = adapter_name == "LocalSegmenter"
    assets = SAM_ASSETS if is_segment else DEPTH_ASSETS
    revision = SAM_REVISION if is_segment else DEPTH_REVISION
    checkpoint = _snapshot(tmp_path, adapter_name, assets, revision)
    processor = (
        _SamProcessor(torch.ones((1, 1, 6, 8), dtype=torch.bool))
        if is_segment
        else _DepthProcessor(torch.ones((6, 8)))
    )
    model = _Model(types.SimpleNamespace())
    fake, *_ = _fake_transformers(
        processor=processor,
        model=model,
        model_type="sam2_video" if is_segment else "depth_anything",
        segmenter=is_segment,
        loading_info={
            "missing_keys": ["vision_encoder.required.weight"],
            "unexpected_keys": [],
            "mismatched_keys": [],
            "error_msgs": [],
        },
    )
    _install_imports(monkeypatch, fake)
    adapter = getattr(module, adapter_name)(checkpoint, device="cpu")

    with pytest.raises(ValueError, match="missing_keys"):
        if is_segment:
            adapter.segment_image(
                Image.new("RGB", (8, 6)),
                ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4)),
            )
        else:
            adapter.predict_depth(Image.new("RGB", (8, 6)))


@pytest.mark.parametrize("adapter_name", ["LocalSegmenter", "LocalRelativeDepth"])
def test_device_transfer_failure_remains_unloadable(
    adapter_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    is_segment = adapter_name == "LocalSegmenter"
    assets = SAM_ASSETS if is_segment else DEPTH_ASSETS
    revision = SAM_REVISION if is_segment else DEPTH_REVISION
    checkpoint = _snapshot(tmp_path, adapter_name, assets, revision)
    processor = (
        _SamProcessor(torch.ones((1, 1, 6, 8), dtype=torch.bool))
        if is_segment
        else _DepthProcessor(torch.ones((6, 8)))
    )
    model = _Model(types.SimpleNamespace(), fail_transfer=True)
    fake, *_ = _fake_transformers(
        processor=processor,
        model=model,
        model_type="sam2_video" if is_segment else "depth_anything",
        segmenter=is_segment,
    )
    _install_imports(monkeypatch, fake)
    adapter = getattr(module, adapter_name)(checkpoint, device="cpu")

    with pytest.raises(RuntimeError, match="device transfer"):
        if is_segment:
            adapter.segment_image(
                Image.new("RGB", (8, 6)),
                ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4)),
            )
        else:
            adapter.predict_depth(Image.new("RGB", (8, 6)))
    adapter.unload()

    assert model.cpu_calls == 1


@pytest.mark.parametrize(
    ("kind", "model_type", "message"),
    [("segment", "sam2", "sam2_video"), ("depth", "sam2", "depth_anything")],
)
def test_checkpoint_model_type_must_match_adapter(
    kind: str,
    model_type: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    is_segment = kind == "segment"
    assets = SAM_ASSETS if is_segment else DEPTH_ASSETS
    revision = SAM_REVISION if is_segment else DEPTH_REVISION
    checkpoint = _snapshot(tmp_path, kind, assets, revision)
    processor = _SamProcessor(torch.zeros((1, 1, 6, 8), dtype=torch.bool))
    model = _Model(types.SimpleNamespace())
    fake, _, _, model_factory = _fake_transformers(
        processor=processor,
        model=model,
        model_type=model_type,
        segmenter=is_segment,
    )
    _install_imports(monkeypatch, fake)
    adapter = (
        module.LocalSegmenter(checkpoint, device="cpu")
        if is_segment
        else module.LocalRelativeDepth(checkpoint, device="cpu")
    )

    with pytest.raises(ValueError, match=message):
        if is_segment:
            adapter.segment_image(
                Image.new("RGB", (8, 6)),
                ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4)),
            )
        else:
            adapter.predict_depth(Image.new("RGB", (8, 6)))
    assert model_factory.calls == []


@pytest.mark.parametrize("bad_score", [math.nan, math.inf])
def test_segmenter_rejects_nonfinite_score(
    bad_score: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, "sam", SAM_ASSETS, SAM_REVISION)
    processor = _SamProcessor(torch.zeros((1, 1, 6, 8), dtype=torch.bool))
    model = _Model(
        types.SimpleNamespace(
            pred_masks=torch.zeros((1, 1, 1, 2, 2)),
            iou_scores=torch.tensor([[[bad_score]]]),
        )
    )
    fake, *_ = _fake_transformers(
        processor=processor, model=model, model_type="sam2_video", segmenter=True
    )
    _install_imports(monkeypatch, fake)

    with pytest.raises(ValueError, match="score"):
        module.LocalSegmenter(checkpoint, device="cpu").segment_image(
            Image.new("RGB", (8, 6)),
            ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4)),
        )


def test_depth_rejects_nonfinite_restored_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    checkpoint = _snapshot(tmp_path, "depth", DEPTH_ASSETS, DEPTH_REVISION)
    restored = torch.zeros((6, 8))
    restored[0, 0] = torch.nan
    processor = _DepthProcessor(restored)
    model = _Model(types.SimpleNamespace(predicted_depth=torch.zeros((1, 2, 2))))
    fake, *_ = _fake_transformers(
        processor=processor, model=model, model_type="depth_anything", segmenter=False
    )
    _install_imports(monkeypatch, fake)

    with pytest.raises(ValueError, match="nonfinite"):
        module.LocalRelativeDepth(checkpoint, device="cpu").predict_depth(
            Image.new("RGB", (8, 6))
        )


@pytest.mark.parametrize("adapter_name", ["LocalSegmenter", "LocalRelativeDepth"])
def test_unload_releases_model_is_idempotent_and_next_call_reloads(
    adapter_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    is_segment = adapter_name == "LocalSegmenter"
    assets = SAM_ASSETS if is_segment else DEPTH_ASSETS
    revision = SAM_REVISION if is_segment else DEPTH_REVISION
    checkpoint = _snapshot(tmp_path, adapter_name, assets, revision)
    models: list[_Model] = []

    def new_model() -> _Model:
        output = (
            types.SimpleNamespace(
                pred_masks=torch.zeros((1, 1, 1, 2, 2)),
                iou_scores=torch.tensor([[[0.5]]]),
            )
            if is_segment
            else types.SimpleNamespace(predicted_depth=torch.zeros((1, 2, 2)))
        )
        model = _Model(output)
        models.append(model)
        return model

    processor = (
        _SamProcessor(torch.ones((1, 1, 6, 8), dtype=torch.bool))
        if is_segment
        else _DepthProcessor(torch.arange(48).reshape(6, 8).float())
    )
    fake, _, _, _ = _fake_transformers(
        processor=processor,
        model=new_model,
        model_type="sam2_video" if is_segment else "depth_anything",
        segmenter=is_segment,
    )
    _install_imports(monkeypatch, fake)
    adapter = getattr(module, adapter_name)(checkpoint, device="cpu")
    image = Image.new("RGB", (8, 6))
    prompt = ImageRegion(width=8, height=6, bbox_xyxy=(1, 1, 5, 4))

    if is_segment:
        adapter.segment_image(image, prompt)
    else:
        adapter.predict_depth(image)
    adapter.unload()
    adapter.unload()
    if is_segment:
        adapter.segment_image(image, prompt)
    else:
        adapter.predict_depth(image)

    assert len(models) == 2
    assert models[0].cpu_calls == 1


def test_adapters_reject_non_rgb_images_before_model_load(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"loaded {name}")),
    )

    with pytest.raises(ValueError, match="RGB"):
        module.LocalRelativeDepth(Path("/not/read"), device="cpu").predict_depth(
            Image.new("L", (8, 6))
        )
