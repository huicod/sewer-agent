#!/usr/bin/env python3
"""Prepare, validate, and explicitly transfer only the seven online model assets."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "deploy" / "models.json"
LICENSE_PATH = (
    REPO_ROOT / "backend/src/pipe_inspector/vision/_enhancement_vendor/LICENSES.md"
)


class BundleError(ValueError):
    """An operational error safe to show without configuration or access tokens."""


def _relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise BundleError("model manifest contains an unsafe relative path")
    return value


def _path(root: Path, relative: str) -> Path:
    root = Path(root).resolve()
    relative = _relative(relative)
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise BundleError(f"model path escapes its directory: {relative}")
    current = root
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise BundleError(f"model paths must not be symbolic links: {relative}")
    return path


def load_manifest(path: Path | None = None) -> dict:
    try:
        manifest = json.loads(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundleError("model manifest is missing or invalid") from error
    asset_entries(manifest)
    return manifest


def asset_entries(manifest: dict) -> list[dict]:
    try:
        models = manifest["models"]
        if manifest["schema_version"] != 1 or len(models) != 7:
            raise BundleError("expected the seven-model online manifest version 1")
        entries = [asset for model in models for asset in model["files"]]
        seen = set()
        for asset in entries:
            name = _relative(asset["path"])
            _relative(asset["source"])
            if name in seen:
                raise BundleError("model manifest contains duplicate paths")
            seen.add(name)
            if not ("generated_json" in asset or "json_fields" in asset):
                size = asset["size_bytes"]
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                    raise BundleError("model weight size must be a positive integer")
        return entries
    except (KeyError, TypeError) as error:
        raise BundleError("model manifest has an invalid structure") from error


def _validate_file(path: Path, asset: dict) -> None:
    name = asset["path"]
    if not path.is_file():
        raise BundleError(f"missing model file: {name}")
    size = path.stat().st_size
    if size == 0:
        raise BundleError(f"empty model file: {name}")
    if "generated_json" in asset or "json_fields" in asset:
        if size > 1024 * 1024:
            raise BundleError(f"model metadata is too large: {name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BundleError(f"invalid model JSON: {name}") from error
        if not isinstance(value, dict):
            raise BundleError(f"model metadata must be an object: {name}")
        if "generated_json" in asset and value != asset["generated_json"]:
            raise BundleError(f"model origin or pinned version differs: {name}")
        for field, expected in asset.get("json_fields", {}).items():
            if value.get(field) != expected:
                raise BundleError(f"model configuration differs at {field}: {name}")
    elif size != asset["size_bytes"]:
        raise BundleError(
            f"model size differs: {name} (expected {asset['size_bytes']}, found {size})"
        )


def check_bundle(directory: Path, manifest_path: Path | None = None, *, manifest=None) -> list[str]:
    """Return file/version errors; never load a model or contact a remote service."""
    manifest = manifest or load_manifest(manifest_path)
    errors = []
    for asset in asset_entries(manifest):
        try:
            _validate_file(_path(directory, asset["path"]), asset)
        except (BundleError, OSError) as error:
            errors.append(str(error))
    return errors


def _require_complete(directory: Path, manifest: dict) -> None:
    errors = check_bundle(directory, manifest=manifest)
    if errors:
        raise BundleError("model bundle is incomplete:\n" + "\n".join(errors))


def _install(directory: Path, asset: dict, writer) -> bool:
    """Publish a validated new file atomically, never replacing an existing file."""
    target = _path(directory, asset["path"])
    if target.exists():
        try:
            _validate_file(target, asset)
        except (BundleError, OSError) as error:
            raise BundleError(
                f"existing file is not reusable; choose a new directory: {asset['path']}"
            ) from error
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".model-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            writer(output)
        _validate_file(temporary, asset)
        # link() refuses an existing destination, unlike replace()/rename().
        os.link(temporary, target)
        os.chmod(target, 0o644)
    except FileExistsError as error:
        raise BundleError(f"model target appeared during preparation: {asset['path']}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _copy(source: Path, target: Path, asset: dict) -> bool:
    _validate_file(source, asset)

    def writer(output):
        with source.open("rb") as stream:
            shutil.copyfileobj(stream, output, length=1024 * 1024)

    return _install(target, asset, writer)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _license_notices(manifest: dict) -> str:
    wanted = {m["code_license_section"] for m in manifest["models"] if "code_license_section" in m}
    if not LICENSE_PATH.is_file():
        raise BundleError("vendored architecture license notices are missing")
    sections = re.split(r"(?=^## )", LICENSE_PATH.read_text(encoding="utf-8"), flags=re.MULTILINE)
    selected = [section for section in sections if section.splitlines()[0][3:] in wanted]
    if len(selected) != len(wanted):
        raise BundleError("required architecture license notices are incomplete")
    return (
        "# Model bundle origin and license notes\n\n"
        "This is a collection with distinct upstream terms, not a single licensed model. "
        "Architecture licenses below do not establish a separate weight license. "
        "See README.md and models.json for each model's origin and known/unknown weight terms.\n\n"
        + "\n".join(selected)
    )


def bundle_metadata(manifest: dict) -> dict[str, bytes]:
    rows = "\n".join(
        f"| {model['name']} | {model['weight_license']} | "
        f"[Original source]({model['source_url']}) |"
        for model in manifest["models"]
    )
    card = (
        "---\nlicense: other\nlicense_name: mixed-upstream-terms\n"
        "license_link: LICENSES.md\ntags:\n- sewer-inspection\n- model-bundle\n---\n\n"
        "# Sewer Agent online model bundle\n\n"
        "Seven pinned models for detector, enhancement, segmentation, and relative depth. "
        "This package contains no training dataset, uploaded user media, database, "
        "or API credentials.\n\n"
        "| Model | Weight terms | Source |\n|---|---|---|\n" + rows + "\n\n"
        "`unknown` means a separate weight license has not been established by the project; "
        "the architecture's code license must not be treated as a blanket weight license. "
        "Preserve original author attribution and consult original source terms "
        "before redistribution.\n\n"
        "Prepare/download/check with scripts/model_bundle.py from the matching Sewer Agent code. "
        "Mount this directory read-only at /models. models.json defines exact paths and revisions; "
        "its byte sizes detect incomplete files, not cryptographic integrity or model quality. "
        "The application loads these trusted local weights and does not download "
        "during inference.\n"
    )
    return {
        "README.md": card.encode("utf-8"),
        "models.json": _json_bytes(manifest),
        "LICENSES.md": _license_notices(manifest).encode("utf-8"),
    }


def _save_metadata(directory: Path, manifest: dict) -> None:
    for name, content in bundle_metadata(manifest).items():
        target = _path(directory, name)
        if target.exists():
            if (
                not target.is_file()
                or target.stat().st_size != len(content)
                or target.read_bytes() != content
            ):
                raise BundleError(
                    f"existing bundle metadata differs; choose a new directory: {name}"
                )
            continue
        with target.open("xb") as output:
            output.write(content)


def prepare_bundle(source_root: Path, output: Path, *, manifest=None) -> None:
    manifest = manifest or load_manifest()
    entries = asset_entries(manifest)
    for asset in entries:
        source = _path(source_root, asset["source"])
        if source.is_relative_to(Path(output).resolve()):
            raise BundleError("bundle output must be separate from the original model directories")
        _validate_file(source, asset)
    for asset in entries:
        _copy(_path(source_root, asset["source"]), output, asset)
    _save_metadata(output, manifest)


def _download_one(directory: Path, asset: dict) -> None:
    if "generated_json" in asset:
        _install(directory, asset, lambda output: output.write(
            _json_bytes(asset["generated_json"])
        ))
        return
    url = asset.get("url", "")
    if not url.startswith(("https://huggingface.co/", "https://github.com/")):
        raise BundleError(f"no approved public source for {asset['path']}")

    def writer(output):
        limit = asset["size_bytes"] if "json_fields" not in asset else 1024 * 1024
        request = urllib.request.Request(url, headers={"User-Agent": "sewer-agent-model-setup/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            count = 0
            while data := response.read(1024 * 1024):
                count += len(data)
                if count > limit:
                    raise BundleError(f"download exceeds expected model size: {asset['path']}")
                output.write(data)

    _install(directory, asset, writer)


def download_bundle(directory: Path, detector: Path, *, manifest=None) -> None:
    manifest = manifest or load_manifest()
    entries = asset_entries(manifest)
    _copy(Path(detector), directory, entries[0])
    for asset in entries[1:]:
        _download_one(directory, asset)
    _require_complete(directory, manifest)
    _save_metadata(directory, manifest)


def _hub():
    try:
        import huggingface_hub
        import huggingface_hub.errors
    except ImportError as error:
        raise BundleError(
            "install huggingface_hub to use Hugging Face transfer commands"
        ) from error
    return huggingface_hub


def _repo_id(repo_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", repo_id):
        raise BundleError("use an explicit Hugging Face owner/repository ID")
    if ".." in repo_id or "--" in repo_id:
        raise BundleError("invalid Hugging Face repository ID")


def upload_operations(directory: Path, *, manifest=None) -> dict[str, Path | bytes]:
    manifest = manifest or load_manifest()
    _require_complete(directory, manifest)
    # Never trust a caller's local README, manifest, or arbitrary directory contents for upload.
    operations = {
        asset["path"]: _path(directory, asset["path"])
        for asset in asset_entries(manifest)
    }
    return operations | bundle_metadata(manifest)


def upload_bundle(directory: Path, repo_id: str, *, public=False, manifest=None) -> dict:
    _repo_id(repo_id)
    operations = upload_operations(directory, manifest=manifest)
    hub = _hub()
    token = hub.get_token()
    if not token:
        raise BundleError(
            "Hugging Face login required: run hf auth login locally; do not paste tokens in chat"
        )
    api = hub.HfApi(token=token)
    try:
        info = api.repo_info(repo_id=repo_id, repo_type="model")
    except hub.errors.RepositoryNotFoundError:
        api.create_repo(repo_id=repo_id, repo_type="model", private=not public)
    else:
        if not info.private and not public:
            raise BundleError(
                "target repository is public; explicit --public authorization is required"
            )
    result = api.create_commit(
        repo_id=repo_id,
        repo_type="model",
        commit_message="Add pinned seven-model Sewer Agent online bundle",
        operations=[hub.CommitOperationAdd(
            path_in_repo=name,
            path_or_fileobj=io.BytesIO(value) if isinstance(value, bytes) else str(value),
        ) for name, value in operations.items()],
    )
    return {"repo_id": repo_id, "revision": result.oid, "commit_url": result.commit_url}


def fetch_bundle(directory: Path, repo_id: str, revision: str, *, manifest=None) -> None:
    _repo_id(repo_id)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise BundleError(
            "fetch requires a full immutable 40-character Hugging Face commit revision"
        )
    manifest = manifest or load_manifest()
    entries = asset_entries(manifest)
    hub = _hub()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    missing = []
    for asset in entries:
        target = _path(directory, asset["path"])
        if target.exists():
            _validate_file(target, asset)
        else:
            missing.append(asset)
    if missing:
        with tempfile.TemporaryDirectory(prefix=".hf-models-", dir=directory) as temporary:
            hub.snapshot_download(
                repo_id=repo_id, repo_type="model", revision=revision,
                local_dir=temporary, allow_patterns=[asset["path"] for asset in missing],
            )
            for asset in missing:
                _copy(_path(Path(temporary), asset["path"]), directory, asset)
    _require_complete(directory, manifest)
    _save_metadata(directory, manifest)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print the seven-model manifest; no network or model loading")
    check = commands.add_parser("check")
    check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--json", action="store_true")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--source-root", type=Path, default=REPO_ROOT)
    download = commands.add_parser("download")
    download.add_argument("--directory", type=Path, required=True)
    download.add_argument("--detector", type=Path, required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("--directory", type=Path, required=True)
    upload.add_argument("--repo-id", required=True)
    upload.add_argument("--public", action="store_true", help="explicitly allow public publication")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--directory", type=Path, required=True)
    fetch.add_argument("--repo-id", required=True)
    fetch.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps(load_manifest(), ensure_ascii=False, indent=2))
        elif args.command == "check":
            errors = check_bundle(args.directory)
            result = {"ready": not errors, "models": 7, "errors": errors}
            print(json.dumps(result, ensure_ascii=False) if args.json else (
                "Seven online models are complete (size and metadata checks)." if not errors
                else "\n".join(errors)
            ))
            return int(bool(errors))
        elif args.command == "prepare":
            prepare_bundle(args.source_root, args.output)
            print("Seven-model bundle prepared; original files unchanged.")
        elif args.command == "download":
            download_bundle(args.directory, args.detector)
            print("Seven-model bundle prepared from pinned sources.")
        elif args.command == "upload":
            print(json.dumps(upload_bundle(args.directory, args.repo_id, public=args.public)))
        elif args.command == "fetch":
            fetch_bundle(args.directory, args.repo_id, args.revision)
            print("Seven-model bundle fetched and validated.")
    except BundleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Remote exception messages may contain request headers or signed URLs.
        print(
            f"error: model operation failed ({type(error).__name__}); no existing files replaced",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
