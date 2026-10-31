#!/usr/bin/env python3
"""Create separate Docker configuration without reading or replacing native secrets."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PASSWORD_FIELDS = (
    "POSTGRES_ADMIN_PASSWORD", "SEWER_APP_DB_PASSWORD", "SEWER_TEMPORAL_DB_PASSWORD",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / ".env.docker")
    parser.add_argument("--models-dir", type=Path)
    args = parser.parse_args()
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
