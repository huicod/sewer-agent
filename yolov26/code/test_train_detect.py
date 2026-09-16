from __future__ import annotations

import builtins
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

from yolov26.code import train_detect


def _write_profile(path: Path, *, train: dict | None = None, resources: dict | None = None) -> Path:
    profile = {
        "train": train
        or {
            "model": "yolov26/pretrained/yolo26m.pt",
            "data": "var/datasets/merged_five_v1/data.yaml",
            "project": "var/training",
            "device": "0",
            "batch": 8,
            "imgsz": 960,
        },
        "resources": resources or {"gpu_memory_gib": 16, "min_free_gpu_gib": 18, "cpu_threads": 4},
    }
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return path


def test_load_profile_resolves_training_paths_from_repository_root(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path / "profile.yaml")

    training, resources = train_detect.load_profile(profile_path)

    repo_root = Path(train_detect.__file__).resolve().parents[2]
    assert training == {
        "model": str(repo_root / "yolov26/pretrained/yolo26m.pt"),
        "data": str(repo_root / "var/datasets/merged_five_v1/data.yaml"),
        "project": str(repo_root / "var/training"),
        "device": "0",
        "batch": 8,
        "imgsz": 960,
    }
    assert resources == {"gpu_memory_gib": 16, "min_free_gpu_gib": 18, "cpu_threads": 4}


@pytest.mark.parametrize(
    ("train_update", "resource_update", "message"),
    [
        ({"batch": 0}, {}, "train.batch"),
        ({"batch": 0.5}, {}, "train.batch"),
        ({}, {"gpu_memory_gib": 0}, "resources.gpu_memory_gib"),
        ({}, {"gpu_memory_gib": float("inf")}, "resources.gpu_memory_gib"),
        ({}, {"min_free_gpu_gib": float("nan")}, "resources.min_free_gpu_gib"),
        ({}, {"min_free_gpu_gib": -1}, "resources.min_free_gpu_gib"),
        ({}, {"cpu_threads": 0}, "resources.cpu_threads"),
    ],
)
def test_load_profile_rejects_nonpositive_resource_and_batch_values(
    tmp_path: Path,
    train_update: dict,
    resource_update: dict,
    message: str,
) -> None:
    train = {
        "model": "model.pt",
        "data": "data.yaml",
        "project": "runs",
        "device": "0",
        "batch": 8,
    }
    train.update(train_update)
    resources = {"gpu_memory_gib": 16, "min_free_gpu_gib": 18, "cpu_threads": 4}
    resources.update(resource_update)
    profile_path = _write_profile(tmp_path / "profile.yaml", train=train, resources=resources)

    with pytest.raises(ValueError, match=message):
        train_detect.load_profile(profile_path)


def test_profile_dry_run_never_imports_torch_or_ultralytics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = _write_profile(tmp_path / "profile.yaml")
    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", 1)[0] in {"torch", "ultralytics"}:
            raise AssertionError(f"dry-run imported {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    train_detect.main(["--profile", str(profile_path), "--dry-run"])

    output = json.loads(capsys.readouterr().out)
    assert output["train"]["batch"] == 8
    assert output["resources"]["gpu_memory_gib"] == 16


def test_profile_rejects_legacy_training_overrides(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path / "profile.yaml")

    with pytest.raises(SystemExit):
        train_detect.main(["--profile", str(profile_path), "--batch", "2", "--dry-run"])


@pytest.mark.parametrize("abbreviated_option", ["--bat", "--ep"])
def test_profile_rejects_abbreviated_legacy_options(
    tmp_path: Path, abbreviated_option: str
) -> None:
    profile_path = _write_profile(tmp_path / "profile.yaml")

    with pytest.raises(SystemExit):
        train_detect.main(["--profile", str(profile_path), abbreviated_option, "2", "--dry-run"])


class _FakeCuda:
    def __init__(self, *, available: bool = True, total_gib: int = 32, free_gib: int = 24) -> None:
        self._available = available
        self._total = total_gib * 1024**3
        self._free = free_gib * 1024**3
        self.device = None
        self.fraction_call = None

    def is_available(self) -> bool:
        return self._available

    def set_device(self, device: int) -> None:
        self.device = device

    def get_device_properties(self, device: int) -> types.SimpleNamespace:
        return types.SimpleNamespace(total_memory=self._total)

    def mem_get_info(self, device: int) -> tuple[int, int]:
        return self._free, self._total

    def set_per_process_memory_fraction(self, fraction: float, device: int) -> None:
        self.fraction_call = (fraction, device)


class _FakeTorch(types.ModuleType):
    def __init__(self, cuda: _FakeCuda) -> None:
        super().__init__("torch")
        self.cuda = cuda
        self.cpu_threads = None

    def set_num_threads(self, count: int) -> None:
        self.cpu_threads = count


def test_apply_resource_limits_sets_cpu_threads_and_cuda_allocator_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_torch = _FakeTorch(_FakeCuda())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    train_detect.apply_resource_limits(
        {"gpu_memory_gib": 16, "min_free_gpu_gib": 18, "cpu_threads": 4}, "0"
    )

    assert fake_torch.cpu_threads == 4
    assert fake_torch.cuda.device == 0
    assert fake_torch.cuda.fraction_call == (0.5, 0)
    assert train_detect.os.environ["OMP_NUM_THREADS"] == "4"
    assert train_detect.os.environ["MKL_NUM_THREADS"] == "4"


@pytest.mark.parametrize(
    ("cuda", "message"),
    [
        (_FakeCuda(available=False), "CUDA is not available"),
        (_FakeCuda(total_gib=12, free_gib=12), "exceeds device 0 total"),
        (_FakeCuda(total_gib=32, free_gib=17), "requires at least 18"),
    ],
)
def test_apply_resource_limits_rejects_unsafe_gpu_preflight(
    monkeypatch: pytest.MonkeyPatch, cuda: _FakeCuda, message: str
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _FakeTorch(cuda))

    with pytest.raises(RuntimeError, match=message):
        train_detect.apply_resource_limits(
            {"gpu_memory_gib": 16, "min_free_gpu_gib": 18, "cpu_threads": 4}, "0"
        )


def test_profile_launch_forwards_only_profile_training_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.pt"
    data_path = tmp_path / "data.yaml"
    model_path.touch()
    data_path.touch()
    train = {
        "model": str(model_path),
        "data": str(data_path),
        "project": str(tmp_path / "runs"),
        "device": "0",
        "batch": 3,
        "custom_option": "kept",
    }
    profile_path = _write_profile(tmp_path / "profile.yaml", train=train)
    calls: dict[str, object] = {}

    class FakeYOLO:
        def __init__(self, model: str) -> None:
            calls["model"] = model

        def train(self, **kwargs: object) -> None:
            calls["train"] = kwargs

    monkeypatch.setattr(train_detect, "apply_resource_limits", lambda resources, device: None)
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

    train_detect.main(["--profile", str(profile_path)])

    assert calls["model"] == str(model_path)
    assert calls["train"] == {
        "data": str(data_path),
        "project": str(tmp_path / "runs"),
        "device": "0",
        "batch": 3,
        "custom_option": "kept",
    }
