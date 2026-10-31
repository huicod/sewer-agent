"""CI-only synthetic model mount and HTTP checks for the Docker deployment smoke job."""

import argparse
import base64
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A real, synthetic 4x3 RGB PNG. Its bytes must survive a full container recreation.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAFElEQVR4nGNMaVnJ"
    "AANMDEgAhQMAOkwBlzSCyAwAAAAASUVORK5CYII="
)


def seed_models(directory: Path) -> None:
    if any(directory.iterdir()):
        raise RuntimeError("CI model mount must be empty")
    manifest = json.loads((ROOT / "deploy/models.json").read_text())
    for model in manifest["models"]:
        for item in model["files"]:
            target = directory / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            metadata = item.get("generated_json", item.get("json_fields"))
            if metadata is not None:
                target.write_text(json.dumps(metadata))
            else:
                # Exercise real mount paths, permissions and size checks. Never load these.
                with target.open("xb") as out:
                    out.write(b"SYNTHETIC CI PLACEHOLDER, NO MODEL PARAMETERS\n")
                    out.truncate(item["size_bytes"])


def request(path: str, *, method="GET", body=None, content_type=None, status=200) -> bytes:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("expected a path on the local test API")
    port = int(os.environ["SEWER_HTTP_PORT"])
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", body, headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.status == status, (path, response.status)
        return response.read()


def seed_records(state_file: Path) -> None:
    session = json.loads(request("/api/sessions", method="POST", body=b"", status=201))
    session_id = session["session_id"]
    request(
        f"/api/sessions/{session_id}",
        method="PATCH",
        body=b'{"title":"Docker smoke"}',
        content_type="application/json",
    )
    boundary = "sewer-ci-png-boundary"
    upload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="synthetic.png"\r\nContent-Type: image/png\r\n\r\n'
    ).encode()
    upload += PNG + f"\r\n--{boundary}--\r\n".encode()
    asset = json.loads(
        request(
            "/api/assets",
            method="POST",
            body=upload,
            content_type=f"multipart/form-data; boundary={boundary}",
            status=201,
        )
    )
    assert (asset["kind"], asset["width"], asset["height"]) == ("image", 4, 3)
    with state_file.open("x") as out:
        json.dump({"session_id": session_id, "asset": asset}, out)


def verify_records(state_file: Path) -> None:
    assert json.loads(request("/health"))["status"] == "alive"
    html = request("/").decode()
    refs = re.findall(r'(?:src|href)="(/assets/[^" ]+\.(?:js|css))"', html)
    for suffix in (".js", ".css"):
        matches = [path for path in refs if path.endswith(suffix)]
        assert matches, f"missing built frontend {suffix}"
        for path in matches:
            assert request(path), f"empty frontend asset {path}"
    state = json.loads(state_file.read_text())
    session_id, asset = state["session_id"], state["asset"]
    summaries = json.loads(request("/api/sessions"))
    assert any(s["session_id"] == session_id and s["title"] == "Docker smoke" for s in summaries)
    detail = json.loads(request(f"/api/sessions/{session_id}"))
    assert detail["session"]["session_id"] == session_id
    prefix = f"/api/assets/{asset['ref']['asset_id']}/versions/{asset['ref']['version']}"
    assert json.loads(request(prefix)) == asset
    assert request(prefix + "/content") == PNG


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("models", "seed", "verify"))
    operation = parser.parse_args().operation
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("RUNNER_TEMP"):
        parser.error("requires a disposable GitHub Actions runner")
    workspace = Path(os.environ["RUNNER_TEMP"]) / "sewer-smoke"
    if operation == "models":
        seed_models(workspace / "models")
    elif operation == "seed":
        seed_records(workspace / "records.json")
    else:
        verify_records(workspace / "records.json")
