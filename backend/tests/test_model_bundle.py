"""Model delivery must keep exact assets, versions, and private upload boundaries."""

import copy
import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from scripts import model_bundle as bundle


@pytest.fixture
def tiny_manifest():
    manifest = copy.deepcopy(bundle.load_manifest())
    for model in manifest["models"]:
        for asset in model["files"]:
            if "json_fields" not in asset and "generated_json" not in asset:
                asset["size_bytes"] = 4
    return manifest


def make_files(root, manifest, *, sources=False):
    for model in manifest["models"]:
        for asset in model["files"]:
            path = root / asset["source" if sources else "path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            data = asset.get("generated_json", asset.get("json_fields"))
            path.write_bytes(json.dumps(data).encode() if data is not None else b"test")


def test_real_manifest_has_only_seven_online_models():
    manifest = bundle.load_manifest()
    assert len(manifest["models"]) == 7
    assert len(bundle.asset_entries(manifest)) == 15
    assert {m["id"] for m in manifest["models"]} == {
        "yolo26s-sewer2403-five", "dehazeformer-mct", "restormer-motion",
        "realesr-general-x4v3", "darkir-384", "sam2.1-hiera-large",
        "depth-anything-v2-small",
    }


def test_check_fails_missing_empty_and_wrong_provenance(tmp_path, tiny_manifest):
    make_files(tmp_path, tiny_manifest)
    assert bundle.check_bundle(tmp_path, manifest=tiny_manifest) == []
    weight = tmp_path / "detector/best.pt"
    weight.write_bytes(b"")
    metadata = tmp_path / "geometry/sam2.1-hiera-large/provenance.json"
    metadata.write_text('{"repo_id":"wrong","revision":"main"}')
    (tmp_path / "geometry/depth-anything-v2-small/config.json").unlink()
    errors = bundle.check_bundle(tmp_path, manifest=tiny_manifest)
    assert len(errors) == 3
    assert any("best.pt" in error for error in errors)
    assert any("provenance.json" in error for error in errors)
    assert any("config.json" in error for error in errors)


def test_check_rejects_malformed_configuration(tmp_path, tiny_manifest):
    make_files(tmp_path, tiny_manifest)
    config = tmp_path / "geometry/sam2.1-hiera-large/config.json"
    config.write_text('{"model_type":"sam2"}')
    assert any("config.json" in e for e in bundle.check_bundle(
        tmp_path, manifest=tiny_manifest
    ))


@pytest.mark.parametrize("bad", ["../.env", "/tmp/secret", "a/../../x", "a\\..\\x"])
def test_manifest_rejects_traversal(bad, tiny_manifest):
    tiny_manifest["models"][0]["files"][0]["path"] = bad
    with pytest.raises(bundle.BundleError):
        bundle.asset_entries(tiny_manifest)


def test_prepare_copies_only_allowlisted_assets_and_reuses(tmp_path, tiny_manifest):
    source, target = tmp_path / "source", tmp_path / "target"
    make_files(source, tiny_manifest, sources=True)
    (source / ".env").write_text("PRIVATE_VALUE=do-not-transfer")
    (source / "var/assets").mkdir(parents=True)
    (source / "var/assets/private.jpg").write_bytes(b"private")
    bundle.prepare_bundle(source, target, manifest=tiny_manifest)
    bundle.prepare_bundle(source, target, manifest=tiny_manifest)
    assert bundle.check_bundle(target, manifest=tiny_manifest) == []
    assert not (target / ".env").exists()
    assert not (target / "var").exists()
    assert (target / "detector/best.pt").read_bytes() == b"test"
    assert (source / tiny_manifest["models"][0]["files"][0]["source"]).read_bytes() == b"test"


def test_prepare_never_overwrites_bad_existing_file(tmp_path, tiny_manifest):
    source, target = tmp_path / "source", tmp_path / "target"
    make_files(source, tiny_manifest, sources=True)
    (target / "detector").mkdir(parents=True)
    path = target / "detector/best.pt"
    path.write_bytes(b"existing model")
    with pytest.raises(bundle.BundleError, match="existing"):
        bundle.prepare_bundle(source, target, manifest=tiny_manifest)
    assert path.read_bytes() == b"existing model"


def test_prepare_rejects_symlink_escape(tmp_path, tiny_manifest):
    source, target, outside = [tmp_path / name for name in ["source", "target", "outside"]]
    make_files(source, tiny_manifest, sources=True)
    target.mkdir()
    outside.mkdir()
    (target / "detector").symlink_to(outside, target_is_directory=True)
    with pytest.raises(bundle.BundleError):
        bundle.prepare_bundle(source, target, manifest=tiny_manifest)
    assert not list(outside.iterdir())


def test_prepare_rejects_output_over_source_tree(tmp_path, tiny_manifest):
    make_files(tmp_path, tiny_manifest, sources=True)
    with pytest.raises(bundle.BundleError, match="separate"):
        bundle.prepare_bundle(tmp_path, tmp_path, manifest=tiny_manifest)
    assert not (tmp_path / "detector").exists()


def test_upload_operations_ignore_unrelated_and_local_readme(tmp_path, tiny_manifest):
    make_files(tmp_path, tiny_manifest)
    (tmp_path / ".env").write_text("PRIVATE_KEY=secret")
    (tmp_path / "README.md").write_text("UNTRUSTED_LOCAL_SECRET")
    operations = bundle.upload_operations(tmp_path, manifest=tiny_manifest)
    assert set(operations) == {
        *(asset["path"] for asset in bundle.asset_entries(tiny_manifest)),
        "README.md", "models.json", "LICENSES.md", "MODEL-NOTICES.md",
    }
    assert b"UNTRUSTED_LOCAL_SECRET" not in operations["README.md"]
    assert b"PRIVATE_KEY" not in b"".join(v for v in operations.values() if isinstance(v, bytes))
    assert b"unknown" in operations["README.md"].lower()


def test_model_card_uses_portable_license_reference(tiny_manifest):
    card = bundle.bundle_metadata(tiny_manifest)["README.md"].decode()
    _, metadata, body = card.split("---\n", 2)
    # The Hub rejects relative license_link metadata, but accepts relative Markdown links.
    urls = [line.partition(":")[2].strip() for line in metadata.splitlines()
            if line.startswith("license_link:")]
    assert all(url.startswith("https://") for url in urls)
    assert "license: other" in metadata.splitlines()
    assert "[Model license notices](LICENSES.md)" in body
    assert "[Weight licenses and attribution](MODEL-NOTICES.md)" in body


class FakeApi:
    def __init__(self, *, existing=None):
        self.existing = existing
        self.created = []
        self.commits = []

    def repo_info(self, **kwargs):
        if self.existing is None:
            raise FakeNotFound()
        return SimpleNamespace(private=self.existing)

    def create_repo(self, **kwargs):
        self.created.append(kwargs)

    def create_commit(self, **kwargs):
        self.commits.append(kwargs)
        return SimpleNamespace(oid="abc123", commit_url="https://huggingface.co/test/models/commit/abc123")


class FakeNotFound(Exception):
    pass


def fake_hub(api):
    return SimpleNamespace(
        get_token=lambda: "not-a-real-token",
        HfApi=lambda **kwargs: api,
        CommitOperationAdd=lambda **kwargs: kwargs,
        errors=SimpleNamespace(RepositoryNotFoundError=FakeNotFound),
    )


def test_new_upload_is_private_and_allowlisted(tmp_path, tiny_manifest, monkeypatch):
    make_files(tmp_path, tiny_manifest)
    api = FakeApi()
    monkeypatch.setattr(bundle, "_hub", lambda: fake_hub(api))
    bundle.upload_bundle(tmp_path, "test/models", manifest=tiny_manifest)
    assert api.created == [{"repo_id": "test/models", "repo_type": "model", "private": True}]
    operations = api.commits[0]["operations"]
    assert len(operations) == 19
    assert all(operation["path_in_repo"] != ".env" for operation in operations)


def test_upload_does_not_make_existing_repo_public(tmp_path, tiny_manifest, monkeypatch):
    make_files(tmp_path, tiny_manifest)
    api = FakeApi(existing=True)
    monkeypatch.setattr(bundle, "_hub", lambda: fake_hub(api))
    bundle.upload_bundle(tmp_path, "test/models", public=True, manifest=tiny_manifest)
    assert api.created == []
    assert len(api.commits) == 1


def test_public_existing_repo_requires_explicit_public_flag(tmp_path, tiny_manifest, monkeypatch):
    make_files(tmp_path, tiny_manifest)
    api = FakeApi(existing=False)
    monkeypatch.setattr(bundle, "_hub", lambda: fake_hub(api))
    with pytest.raises(bundle.BundleError, match="public"):
        bundle.upload_bundle(tmp_path, "test/models", manifest=tiny_manifest)
    assert api.created == []
    assert api.commits == []


def test_download_uses_pinned_sources_and_creates_provenance(
    tmp_path, tiny_manifest, monkeypatch
):
    target = tmp_path / "bundle"
    detector = tmp_path / "detector.pt"
    detector.write_bytes(b"test")
    responses = {}
    for asset in bundle.asset_entries(tiny_manifest):
        if "url" in asset:
            responses[asset["url"]] = (
                json.dumps(asset["json_fields"]).encode() if "json_fields" in asset else b"test"
            )
    requested = []

    def urlopen(request, timeout):
        requested.append(request.full_url)
        return io.BytesIO(responses[request.full_url])

    monkeypatch.setattr(bundle.urllib.request, "urlopen", urlopen)
    bundle.download_bundle(target, detector, manifest=tiny_manifest)
    assert bundle.check_bundle(target, manifest=tiny_manifest) == []
    assert len(requested) == 12
    provenance = json.loads((target / "geometry/sam2.1-hiera-large/provenance.json").read_text())
    assert provenance["revision"] == "665f8e2ad61cf5f53d65644ff27c8ee525124610"
    bundle.download_bundle(target, detector, manifest=tiny_manifest)
    assert len(requested) == 12


def test_incomplete_download_is_not_published(tmp_path, tiny_manifest, monkeypatch):
    asset = tiny_manifest["models"][1]["files"][0]
    monkeypatch.setattr(bundle.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"bad"))
    with pytest.raises(bundle.BundleError, match="size differs"):
        bundle._download_one(tmp_path, asset)
    assert not (tmp_path / asset["path"]).exists()
    assert not list(tmp_path.rglob(".model-*"))


def test_upload_requires_local_auth_before_creating_repo(tmp_path, tiny_manifest, monkeypatch):
    make_files(tmp_path, tiny_manifest)
    api = FakeApi()
    hub = fake_hub(api)
    hub.get_token = lambda: None
    monkeypatch.setattr(bundle, "_hub", lambda: hub)
    with pytest.raises(bundle.BundleError, match="login required"):
        bundle.upload_bundle(tmp_path, "test/models", manifest=tiny_manifest)
    assert api.created == []
    assert api.commits == []


def test_fetch_ignores_unrelated_remote_files(tmp_path, tiny_manifest, monkeypatch):
    requested = []

    def snapshot_download(**kwargs):
        requested.append(kwargs)
        root = bundle.Path(kwargs["local_dir"])
        make_files(root, tiny_manifest)
        (root / ".env").write_text("UNRELATED_SECRET")

    monkeypatch.setattr(bundle, "_hub", lambda: SimpleNamespace(
        snapshot_download=snapshot_download
    ))
    bundle.fetch_bundle(tmp_path, "test/models", "a" * 40, manifest=tiny_manifest)
    assert bundle.check_bundle(tmp_path, manifest=tiny_manifest) == []
    assert not (tmp_path / ".env").exists()
    assert set(requested[0]["allow_patterns"]) == {
        asset["path"] for asset in bundle.asset_entries(tiny_manifest)
    }
    bundle.fetch_bundle(tmp_path, "test/models", "a" * 40, manifest=tiny_manifest)
    assert len(requested) == 1


def test_fetch_rejects_floating_revision(tmp_path):
    with pytest.raises(bundle.BundleError, match="immutable"):
        bundle.fetch_bundle(tmp_path, "test/models", "main")


@pytest.fixture
def public_release(tmp_path, tiny_manifest, monkeypatch):
    entries = bundle.asset_entries(tiny_manifest)
    data = {}
    checksums = []
    for asset in entries:
        name = asset["path"]
        meta = asset.get("generated_json", asset.get("json_fields"))
        data[name] = json.dumps(meta).encode() if meta is not None else b"test"
        if meta is None:
            checksums.append(f"{hashlib.sha256(data[name]).hexdigest()}  {name}\n")
    checksum_path = tmp_path / "release.sha256"
    checksum_path.write_text("".join(checksums))
    revision_file = tmp_path / "revision.txt"
    revision_file.write_text("a" * 40 + "\n")
    monkeypatch.setattr(bundle, "CHECKSUM_PATH", checksum_path)
    monkeypatch.setattr(bundle, "REVISION_PATH", revision_file)
    monkeypatch.setattr(bundle, "load_manifest", lambda: tiny_manifest)
    monkeypatch.setenv("HF_TOKEN", "must-never-be-sent")
    monkeypatch.setattr(bundle, "_hub", lambda: pytest.fail("public fetch accessed the HF SDK"))
    requests = []

    def urlopen(request, timeout):
        prefix = f"https://huggingface.co/{bundle.PUBLIC_REPO}/resolve/{'a' * 40}/"
        assert request.full_url.startswith(prefix)
        assert request.get_header("Authorization") is None
        name = request.full_url.removeprefix(prefix)
        requests.append(name)
        return io.BytesIO(data[name])

    monkeypatch.setattr(bundle.urllib.request, "urlopen", urlopen)
    return data, requests


def test_public_fetch_verifies_new_bytes_before_install_and_reuses_completed_files(
    tmp_path, public_release,
):
    data, requests = public_release
    target = tmp_path / "models"
    weight = "enhancement/dehazeformer-mct-8e0b26c7.pth"
    data[weight] = b"FAKE"  # The size is correct but the checksum belongs to the release.
    with pytest.raises(bundle.BundleError, match="SHA-256 differs"):
        bundle.public_fetch_bundle(target)
    assert (target / "detector/best.pt").read_bytes() == b"test"
    assert not (target / weight).exists()
    assert not list(target.rglob(".model-*"))

    data[weight] = b"test"
    bundle.public_fetch_bundle(target)
    assert requests.count("detector/best.pt") == 1
    assert set(requests) == set(data)
    assert all((target / name).read_bytes() == value for name, value in data.items())
    assert (target / "LICENSES.md").is_file()

    requests.clear()
    bundle.public_fetch_bundle(target)
    assert not requests
    (target / weight).write_bytes(b"FAKE")
    with pytest.raises(bundle.BundleError, match="existing file is not reusable"):
        bundle.public_fetch_bundle(target)
    assert (target / weight).read_bytes() == b"FAKE"
    assert not requests


def test_incomplete_release_checksums_refuse_download(tmp_path, public_release):
    _, requests = public_release
    listed = bundle.CHECKSUM_PATH.read_text().splitlines(keepends=True)
    bundle.CHECKSUM_PATH.write_text("".join(listed[1:]))
    with pytest.raises(bundle.BundleError, match="checksums do not match"):
        bundle.public_fetch_bundle(tmp_path / "models")
    assert not requests


def test_public_fetch_rejects_unpinned_release(tmp_path, public_release):
    _, requests = public_release
    bundle.REVISION_PATH.write_text("main\n")
    with pytest.raises(bundle.BundleError, match="immutable"):
        bundle.public_fetch_bundle(tmp_path / "models")
    assert not requests


def test_metadata_upgrade_preserves_unrecognized_content(tmp_path, tiny_manifest, monkeypatch):
    card = tmp_path / "README.md"
    old_card = b"previously generated model card"
    card.write_bytes(old_card)
    old_digest = hashlib.sha256(old_card).hexdigest()
    monkeypatch.setitem(bundle.LEGACY_METADATA_SHA256, "README.md", old_digest)
    bundle._save_metadata(tmp_path, tiny_manifest)
    assert "MODEL-NOTICES.md" in card.read_text()
    assert (tmp_path / "MODEL-NOTICES.md").read_bytes() == bundle.NOTICES_PATH.read_bytes()
    card.write_text("my custom model card")
    with pytest.raises(bundle.BundleError, match="existing bundle metadata differs"):
        bundle._save_metadata(tmp_path, tiny_manifest)
    assert card.read_text() == "my custom model card"
