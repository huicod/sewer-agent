from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_PATH_KEYS = ("model", "data", "project")
_RESOURCE_KEYS = ("gpu_memory_gib", "min_free_gpu_gib", "cpu_threads")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train YOLO26 detection on the converted SEDD dataset.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--profile", type=Path, help="YAML profile containing train and resources mappings."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and validate a profile without importing Torch or Ultralytics.",
    )
    parser.add_argument("--data", type=Path, default=Path("runs_data/sedd_yolo/sedd.yaml"))
    parser.add_argument(
        "--model",
        default="yolo26n.pt",
        help="YOLO26 weight, e.g. yolo26n.pt/yolo26s.pt/yolo26m.pt.",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--batch", type=int, default=-1, help="-1 lets Ultralytics auto-select batch size."
    )
    parser.add_argument(
        "--device", default=None, help="Examples: 0, cpu. Defaults to Ultralytics auto selection."
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", default="runs_detect")
    parser.add_argument("--name", default=None)
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument(
        "--cache", action="store_true", help="Cache images if RAM/disk budget allows."
    )
    parser.add_argument("--pretrained", action="store_true", default=True)
    parser.add_argument("--no-plots", action="store_true", help="Disable Ultralytics metric plots.")
    parser.add_argument(
        "--degrees", type=float, default=None, help="Random rotation range in degrees."
    )
    parser.add_argument(
        "--translate", type=float, default=None, help="Random translation fraction."
    )
    parser.add_argument("--scale", type=float, default=None, help="Random scale gain.")
    parser.add_argument(
        "--mosaic", type=float, default=None, help="Mosaic augmentation probability."
    )
    parser.add_argument(
        "--close-mosaic", type=int, default=None, help="Disable mosaic for the final N epochs."
    )
    parser.add_argument("--hsv-h", type=float, default=None, help="HSV hue augmentation gain.")
    parser.add_argument(
        "--hsv-s", type=float, default=None, help="HSV saturation augmentation gain."
    )
    parser.add_argument("--hsv-v", type=float, default=None, help="HSV value augmentation gain.")
    parser.add_argument("--fliplr", type=float, default=None, help="Horizontal flip probability.")
    parser.add_argument("--flipud", type=float, default=None, help="Vertical flip probability.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _is_positive_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_resources(resources: dict[str, Any]) -> None:
    for key in _RESOURCE_KEYS:
        if key not in resources:
            raise ValueError(f"Profile is missing resources.{key}")
        if not _is_positive_finite_number(resources[key]):
            raise ValueError(f"resources.{key} must be a positive number")
    if not isinstance(resources["cpu_threads"], int):
        raise ValueError("resources.cpu_threads must be a positive integer")


def load_profile(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a training profile and resolve its paths from the repository root."""
    import yaml

    with path.expanduser().open(encoding="utf-8") as profile_file:
        profile = yaml.safe_load(profile_file)
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a mapping")

    training = profile.get("train")
    resources = profile.get("resources")
    if not isinstance(training, dict):
        raise ValueError("Profile train must be a mapping")
    if not isinstance(resources, dict):
        raise ValueError("Profile resources must be a mapping")

    training = dict(training)
    resources = dict(resources)
    for key in _PROFILE_PATH_KEYS:
        value = training.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"train.{key} must be a non-empty path string")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        training[key] = str(candidate.resolve())

    if not _is_positive_int(training.get("batch")):
        raise ValueError("train.batch must be a positive integer")
    _validate_resources(resources)
    return training, resources


def _cuda_device_index(device: object) -> int:
    if isinstance(device, bool) or str(device) != "0":
        raise ValueError("Profile training requires explicit CUDA device 0")
    return 0


def apply_resource_limits(resources: dict[str, Any], device: object) -> None:
    """Apply CPU limits and a PyTorch allocator cap after CUDA preflight."""
    _validate_resources(resources)
    device_index = _cuda_device_index(device)
    cpu_threads = int(resources["cpu_threads"])
    os.environ["OMP_NUM_THREADS"] = str(cpu_threads)
    os.environ["MKL_NUM_THREADS"] = str(cpu_threads)

    import torch

    torch.set_num_threads(cpu_threads)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available; profile training requires device 0")

    total_bytes = int(torch.cuda.get_device_properties(device_index).total_memory)
    budget_bytes = float(resources["gpu_memory_gib"]) * 1024**3
    if budget_bytes > total_bytes:
        raise RuntimeError(
            f"GPU allocator budget {resources['gpu_memory_gib']} GiB exceeds device 0 total "
            f"{total_bytes / 1024**3:.2f} GiB"
        )

    free_bytes, _ = torch.cuda.mem_get_info(device_index)
    min_free_bytes = float(resources["min_free_gpu_gib"]) * 1024**3
    if free_bytes < min_free_bytes:
        raise RuntimeError(
            f"GPU device 0 has {free_bytes / 1024**3:.2f} GiB free; profile requires at least "
            f"{resources['min_free_gpu_gib']} GiB"
        )

    torch.cuda.set_device(device_index)
    torch.cuda.set_per_process_memory_fraction(budget_bytes / total_bytes, device_index)


def _reject_profile_legacy_overrides(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    profile_options = {"--profile", "--dry-run", "-h", "--help"}
    legacy_options = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option not in profile_options
    }
    overrides = sorted(
        {token.split("=", 1)[0] for token in argv if token.split("=", 1)[0] in legacy_options}
    )
    if overrides:
        parser.error(
            f"--profile cannot be combined with legacy training options: {', '.join(overrides)}"
        )


def _run_profile(args: argparse.Namespace) -> None:
    training, resources = load_profile(args.profile)
    if args.dry_run:
        print(json.dumps({"train": training, "resources": resources}, indent=2, sort_keys=True))
        return

    model_path = Path(training["model"])
    data_path = Path(training["data"])
    if not model_path.is_file():
        raise FileNotFoundError(f"Model checkpoint not found: {model_path}")
    if not data_path.is_file():
        raise FileNotFoundError(f"Data yaml not found: {data_path}")

    apply_resource_limits(resources, training.get("device"))
    from ultralytics import YOLO

    model_source = training.pop("model")
    model = YOLO(model_source)
    model.train(**training)


def _run_legacy(args: argparse.Namespace) -> None:
    data_path = args.data.resolve()
    if not data_path.exists():
        raise FileNotFoundError(
            f"Data yaml not found: {data_path}. Run prepare_sedd_yolo.py first."
        )

    from ultralytics import YOLO

    run_name = args.name or f"sedd_{Path(args.model).stem}_img{args.imgsz}"
    model = YOLO(args.model)
    train_kwargs = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "project": str(Path(args.project).resolve()),
        "name": run_name,
        "patience": args.patience,
        "cache": args.cache,
        "pretrained": args.pretrained,
        "plots": not args.no_plots,
    }
    if args.device is not None:
        train_kwargs["device"] = args.device
    for key in (
        "degrees",
        "translate",
        "scale",
        "mosaic",
        "close_mosaic",
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "fliplr",
        "flipud",
    ):
        value = getattr(args, key)
        if value is not None:
            train_kwargs[key] = value

    model.train(**train_kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(actual_argv)
    if args.profile is not None:
        _reject_profile_legacy_overrides(parser, actual_argv)
        _run_profile(args)
        return
    if args.dry_run:
        parser.error("--dry-run requires --profile")
    _run_legacy(args)


if __name__ == "__main__":
    main()
