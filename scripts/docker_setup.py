#!/usr/bin/env python3
"""Create Docker configuration or fetch the models and start the deployment."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
PASSWORD_FIELDS = (
    "POSTGRES_ADMIN_PASSWORD",
    "SEWER_APP_DB_PASSWORD",
    "SEWER_TEMPORAL_DB_PASSWORD",
)


def create_configuration(output: Path, models_dir: Path | None = None) -> None:
    template = (REPO_ROOT / ".env.docker.example").read_text(encoding="utf-8")
    for field in PASSWORD_FIELDS:
        template = template.replace(f"{field}=GENERATE", f"{field}={secrets.token_hex(24)}")
    if models_dir is not None:
        location = str(models_dir.expanduser().resolve())
        if any(char in location for char in "\n\r\x00'$"):
            raise ValueError("model directory cannot contain newlines, quotes or dollar signs")
        template = template.replace("SEWER_MODELS_DIR=./models", f"SEWER_MODELS_DIR='{location}'")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(template)


def needs_shared_key(environment: dict) -> bool:
    """Prompt only for a Qwen role that can use the application's shared-key rule."""
    missing = False
    for role in ("LLM", "VLM"):
        prefix = f"SEWER_{role}_"
        base, model = environment.get(prefix + "BASE_URL"), environment.get(prefix + "MODEL")
        if not base or not model:
            raise ValueError(f"Set {prefix}BASE_URL and {prefix}MODEL in the Docker configuration.")
        if environment.get(prefix + "API_KEY"):
            continue
        url = urlsplit(base)
        host = url.hostname or ""
        official = host in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "cn-hongkong.dashscope.aliyuncs.com",
        } or host.endswith(".maas.aliyuncs.com")
        if (
            not official
            or not model.startswith("qwen")
            or url.scheme != "https"
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(f"Set {prefix}API_KEY for this provider in the Docker configuration.")
        missing = missing or not environment.get("DASHSCOPE_API_KEY")
    return missing


def store_shared_key(output: Path, key: str) -> None:
    # Only update the exact empty placeholder from our template. Never reinterpret custom dotenv.
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        raise ValueError("Set DASHSCOPE_API_KEY in the Docker configuration and retry.")
    original = output.read_bytes()
    placeholder = b"DASHSCOPE_API_KEY="
    lines = original.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(placeholder)]
    if len(matches) != 1 or lines[matches[0]].rstrip(b"\r\n") != placeholder:
        raise ValueError("Set DASHSCOPE_API_KEY in the existing Docker configuration and retry.")
    i = matches[0]
    lines[i] = lines[i].replace(placeholder, placeholder + key.encode(), 1)
    fd, filename = tempfile.mkstemp(dir=output.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"".join(lines))
        if output.is_symlink() or output.read_bytes() != original:
            raise ValueError("The Docker configuration changed during setup. Please retry.")
        os.replace(temporary, output)  # replace atomically with a new 0600 file
    finally:
        temporary.unlink(missing_ok=True)


def deploy(output: Path, models_dir: Path | None, services: list[str]) -> None:
    if output.name == ".env" or output.is_symlink():
        raise ValueError("Choose a dedicated Docker configuration file, such as .env.docker.")
    # Serialize two concurrent first launches, including config creation and initialization.
    descriptor = os.open(REPO_ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another Docker setup is running. Retry when it finishes.") from None
        if not output.exists():
            create_configuration(output, models_dir)
        compose = [
            "docker",
            "compose",
            "--env-file",
            str(output.absolute()),
            "-f",
            str(REPO_ROOT / "compose.yaml"),
        ]
        # The saved shared key takes precedence over a stray key from a different shell/project.
        compose_env = os.environ.copy()
        supplied_key = compose_env.pop("DASHSCOPE_API_KEY", "")
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            env=compose_env,
            capture_output=True,
        )
        if daemon.returncode:
            raise ValueError("Cannot reach Docker Engine. Start it and check Docker access.")
        result = subprocess.run(
            [*compose, "config", "--format", "json"],
            cwd=REPO_ROOT,
            env=compose_env,
            capture_output=True,
        )
        if result.returncode:
            raise ValueError("Docker Compose could not read the configuration. Check its settings.")
        api = json.loads(result.stdout)["services"]["api"]
        mount = next(
            (
                v["source"]
                for v in api["volumes"]
                if v.get("target") == "/models" and v.get("type") == "bind"
            ),
            None,
        )
        if not mount or (models_dir and Path(mount) != models_dir.expanduser().resolve()):
            raise ValueError("Check SEWER_MODELS_DIR in the Docker configuration.")
        if needs_shared_key(api["environment"]):
            if not supplied_key:
                if not sys.stdin.isatty():
                    raise ValueError(
                        "Set DASHSCOPE_API_KEY or run setup in an interactive terminal."
                    )
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    supplied_key = getpass.getpass("DashScope API Key (hidden): ")
            store_shared_key(output, supplied_key)
        # The public model host needs no application credentials or Hugging Face token.
        network_env = {
            k: v
            for k, v in os.environ.items()
            if k.lower()
            in {
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "ssl_cert_file",
                "ssl_cert_dir",
                "path",
                "lang",
                "lc_all",
            }
        }
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/model_bundle.py"),
                "fetch-public",
                "--directory",
                mount,
            ],
            cwd=REPO_ROOT,
            env=network_env,
            check=True,
        )
        subprocess.run(
            [*compose, "build", "initialize"], cwd=REPO_ROOT, env=compose_env, check=True
        )
        subprocess.run(
            [*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "360", *services],
            cwd=REPO_ROOT,
            env=compose_env,
            check=True,
        )
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        choices=["up"],
        help="set up models and start Docker (default: only create configuration)",
    )
    parser.add_argument("--output", type=Path, default=REPO_ROOT / ".env.docker")
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument(
        "--services",
        nargs="+",
        choices=["api", "cpu-worker", "gpu-worker"],
        help="start only these services and their dependencies (up only)",
    )
    args = parser.parse_args()
    if args.action == "up":
        try:
            deploy(args.output, args.models_dir, args.services or [])
        except subprocess.CalledProcessError as error:
            parser.exit(1, f"Deployment failed (exit {error.returncode}); see the step above.\n")
        except (OSError, EOFError, getpass.GetPassWarning):
            parser.exit(
                1, "Check Docker Compose v2, configuration permissions and terminal input.\n"
            )
        except ValueError as error:
            parser.exit(1, f"{error}\n")
        print("Docker services are ready. See DOCKER.md for browser access.")
        return 0
    if args.services:
        parser.error("--services requires up")
    try:
        create_configuration(args.output, args.models_dir)
    except FileExistsError:
        parser.exit(1, "Configuration exists; edit it in place to keep database passwords.\n")
    except (OSError, ValueError):
        parser.exit(1, "Cannot create configuration; check the destination and model directory.\n")
    print(f"Created {args.output}. Fill in your API settings, then follow DOCKER.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
