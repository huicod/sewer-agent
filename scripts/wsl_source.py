#!/usr/bin/env python3
"""Install only the source files managed by the Windows/WSL launcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

MARKER = ".sewer-wsl-install.json"
FILES = {
    "Dockerfile",
    "compose.yaml",
    "pyproject.toml",
    ".dockerignore",
    ".env.docker.example",
    "README.md",
    "DOCKER.md",
    "WINDOWS.md",
}
DIRECTORIES = {"backend", "frontend", "scripts", "deploy", "example"}
EXCLUDED = {"node_modules", "dist", "__pycache__", ".venv", ".git", "var", "models"}


def installation_path(value: str, home: Path | None = None) -> Path:
    home = (home or Path.home()).resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = home / path
    # A dedicated directory inside this Ubuntu user's home is intentional.
    resolved = path.resolve()
    if resolved == home or not resolved.is_relative_to(home):
        raise ValueError("InstallDir must be a dedicated directory inside the Ubuntu user's home.")
    for parent in (path, *path.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError("InstallDir must not contain symbolic links.")
    return resolved


def allowed_file(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in name:
        return False
    if any(part in EXCLUDED for part in path.parts):
        return False
    if any(part.startswith(".env") for part in path.parts) and name != ".env.docker.example":
        return False
    return name in FILES or path.parts[0] in DIRECTORIES


def load_marker(destination: Path) -> dict | None:
    path = destination / MARKER
    if path.is_symlink():
        raise ValueError("Installation marker must not be a symbolic link.")
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("version") != 1 or not isinstance(data.get("files"), list):
        raise ValueError("Unrecognized installation marker; keep the existing directory intact.")
    if any(not isinstance(name, str) or not allowed_file(name) for name in data["files"]):
        raise ValueError("Invalid managed source manifest.")
    return data


def safe_target(destination: Path, name: str) -> Path:
    target = destination / name
    if not target.resolve().is_relative_to(destination):
        raise ValueError("Managed source path escapes InstallDir.")
    for part in (target, *target.parents):
        if part == destination:
            break
        if part.is_symlink():
            raise ValueError("Managed source files must not contain symbolic links.")
    return target


def running_project(destination: Path) -> bool:
    if not (destination / ".env.docker").exists():
        return False
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env.docker", "ps", "--status", "running", "-q"],
        cwd=destination,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError("Cannot check existing containers. Start Docker before updating source.")
    return bool(result.stdout.strip())


def write_manifest(path: Path, names: set[str]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump({"version": 1, "files": sorted(names)}, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_source(archive: Path, destination: Path) -> tuple[int, bool]:
    marker = load_marker(destination)
    if destination.exists() and marker is None and any(destination.iterdir()):
        raise ValueError(
            "InstallDir already contains files not managed by this installer; "
            "choose a new directory."
        )
    with tempfile.TemporaryDirectory(prefix="sewer-source-") as temporary:
        staged = Path(temporary)
        names: list[str] = []
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                name = member.name.removeprefix("./")
                if member.isdir():
                    continue
                if not member.isfile() or not allowed_file(name) or name in names:
                    raise ValueError(
                        "Source archive contains an unexpected path, link or duplicate."
                    )
                file = staged / name
                file.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, file.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                # Git checkouts on Windows can use CRLF. Container entry scripts require LF.
                if file.suffix == ".sh":
                    file.write_bytes(file.read_bytes().replace(b"\r\n", b"\n"))
                names.append(name)
        if not {"compose.yaml", "Dockerfile", "scripts/docker_setup.py", "pyproject.toml"}.issubset(
            names
        ):
            raise ValueError("The source archive is incomplete; use the full downloaded project.")
        previous = set(marker["files"] if marker else [])
        changed = previous != set(names)
        for name in names:
            target = safe_target(destination, name)
            changed |= not target.is_file() or target.read_bytes() != (staged / name).read_bytes()
        if marker and changed and running_project(destination):
            raise ValueError(
                "Source changed while this project is running. "
                "Back up, run -Action stop, then repeat setup."
            )
        destination.mkdir(parents=True, exist_ok=True)
        # Record the union first so an interrupted copy remains resumable and managed.
        marker_path = destination / MARKER
        write_manifest(marker_path, previous | set(names))
        for name in names:
            target = safe_target(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, filename = tempfile.mkstemp(prefix=".source-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write((staged / name).read_bytes())
                os.chmod(filename, 0o755 if target.suffix == ".sh" else 0o644)
                os.replace(filename, target)
            finally:
                Path(filename).unlink(missing_ok=True)
        for name in previous - set(names):
            safe_target(destination, name).unlink(missing_ok=True)
        write_manifest(marker_path, set(names))
        return len(names), changed


def initialize_environment(destination: Path, port: int, project: str) -> None:
    path = destination / ".env.docker"
    if path.exists():
        if path.is_symlink():
            raise ValueError("Existing Docker configuration must not be a symbolic link.")
        print("Existing Docker settings and database passwords preserved.")
        return
    temporary = destination / f".env.docker.setup-{uuid.uuid4().hex}"
    try:
        subprocess.run(
            ["python3", "scripts/docker_setup.py", "--output", str(temporary)],
            cwd=destination,
            check=True,
        )
        content = temporary.read_text()
        for old, new in {
            "COMPOSE_PROJECT_NAME=sewer-agent": f"COMPOSE_PROJECT_NAME={project}",
            "SEWER_IMAGE_TAG=local": f"SEWER_IMAGE_TAG={project}",
            "SEWER_HTTP_PORT=8000": f"SEWER_HTTP_PORT={port}",
        }.items():
            if old not in content.splitlines():
                raise ValueError(
                    "Docker configuration template changed; "
                    "inspect the generated settings before proceeding."
                )
            content = content.replace(old + "\n", new + "\n", 1)
        temporary.write_text(content)
        temporary.chmod(0o600)
        # Publish without overwriting a configuration created by another process.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def with_systemd_enabled(text: str) -> str:
    """Preserve unrelated settings and comments while updating only boot.systemd."""
    import re

    lines = text.splitlines(keepends=True)
    in_boot = False
    boot_end: int | None = None
    for index, line in enumerate(lines):
        section = re.match(r"\s*\[([^]]+)\]", line)
        if section:
            if in_boot:
                boot_end = index
                break
            in_boot = section.group(1).strip().lower() == "boot"
        elif in_boot and re.match(r"\s*systemd\s*=", line, re.IGNORECASE):
            lines[index] = "systemd=true\n"
            return "".join(lines)
    if in_boot:
        position = boot_end if boot_end is not None else len(lines)
        if position and not lines[position - 1].endswith(("\n", "\r")):
            lines[position - 1] += "\n"
        lines.insert(position, "systemd=true\n")
        return "".join(lines)
    return text.rstrip("\r\n") + "\n\n[boot]\nsystemd=true\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("path", "install", "configure", "check", "systemd"))
    parser.add_argument("--directory", default="sewer-agent")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--project", default="sewer-agent-wsl")
    args = parser.parse_args()
    try:
        if args.action == "systemd":
            path = Path("/etc/wsl.conf")
            original = path.read_text() if path.exists() else ""
            updated = with_systemd_enabled(original)
            if updated != original:
                if path.exists():
                    fd, backup = tempfile.mkstemp(prefix="wsl.conf.sewer-backup-", dir=path.parent)
                    with os.fdopen(fd, "w") as output:
                        output.write(original)
                path.write_text(updated)
                path.chmod(0o644)
            return
        destination = installation_path(args.directory)
        if args.action == "path":
            print(destination)
        elif args.action == "install":
            if not args.archive:
                raise ValueError("A source archive is required.")
            count, changed = install_source(args.archive, destination)
            print(
                f"Source {'installed/updated' if changed else 'verified unchanged'}: "
                f"{count} files in {destination}"
            )
        elif args.action == "configure":
            import re

            if not 1024 <= args.port <= 65535 or not re.fullmatch(
                r"[a-z0-9][a-z0-9_-]{0,45}", args.project
            ):
                raise ValueError("Invalid port or Compose project name.")
            initialize_environment(destination, args.port, args.project)
        elif not load_marker(destination) or not (destination / ".env.docker").is_file():
            raise ValueError("No managed installation found; run setup first.")
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
