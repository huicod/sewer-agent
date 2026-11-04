"""Settings are shared across processes without changing already admitted runs."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.runtime_settings import (
    RuntimeSettingsError,
    RuntimeSettingsStore,
    SettingsUpdate,
    current_api_settings,
    settings_for_run,
)
from pipe_inspector.workflow.conversation_start import turn_config
from pipe_inspector.workflow.start import run_config
from pydantic import SecretStr


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        runtime_settings_dir=tmp_path / "settings",
        llm_base_url="https://old.example/v1",
        llm_model="old-text",
        llm_api_key=SecretStr("old-text-secret"),
        vlm_base_url="https://old.example/v1",
        vlm_model="old-vision",
        vlm_api_key=SecretStr("old-vision-secret"),
        dashscope_api_key=None,
    )


def update(revision=0, **overrides):
    return SettingsUpdate.model_validate(
        {
            "revision": revision,
            "llm": {
                "base_url": "https://new.example/v1",
                "model": "new-text",
                "api_key": "new-text-secret",
            },
            "vlm": {
                "base_url": "https://new.example/v1",
                "model": "new-vision",
                "api_key": "new-vision-secret",
            },
            "api_timeout_seconds": 90,
            "api_max_output_tokens": 2048,
            "api_enable_thinking": False,
            **overrides,
        }
    )


def test_saved_revision_is_visible_to_new_instances_and_pins_old_requests(settings):
    store = RuntimeSettingsStore(settings)
    old = current_api_settings(settings)
    old_turn = turn_config(old)
    old_run = run_config(old)
    result = store.save(update())
    assert result["revision"] == 1
    assert "secret" not in json.dumps(result)
    fresh_base = Settings(**settings.model_dump(), _env_file=None)
    current = current_api_settings(fresh_base)
    assert current.llm_model == "new-text"
    assert current.vlm_api_key.get_secret_value() == "new-vision-secret"
    assert turn_config(settings_for_run(fresh_base, old_turn)) == old_turn
    assert (
        settings_for_run(fresh_base, old_run).vlm_api_key.get_secret_value() == "old-vision-secret"
    )
    assert settings_for_run(fresh_base, {}).vlm_model == "old-vision"
    assert old.llm_model == "old-text"


@pytest.mark.parametrize("admission", [run_config, turn_config])
def test_first_admission_freezes_revision_zero_before_any_settings_save(settings, admission):
    store = RuntimeSettingsStore(settings)
    assert store.public()["revision"] == 0
    assert not store.path.exists()  # GET stays read-only.
    config = admission(settings)
    assert config["api_settings_revision"] == 0
    assert store.path.is_file()
    changed_environment = settings.model_copy(
        update={
            "llm_api_key": SecretStr("rotated-text-secret"),
            "vlm_api_key": SecretStr("rotated-vision-secret"),
        }
    )
    worker = settings_for_run(changed_environment, config)
    assert worker.llm_api_key.get_secret_value() == "old-text-secret"
    assert worker.vlm_api_key.get_secret_value() == "old-vision-secret"
    repeated = admission(changed_environment)
    assert repeated["api_settings_revision"] == config["api_settings_revision"]
    for name in ("llm_model", "llm_base_url", "vlm_model", "vlm_base_url"):
        assert repeated.get(name) == config.get(name)


def test_concurrent_first_admissions_commit_one_immutable_baseline(settings):
    other = settings.model_copy(update={"llm_api_key": SecretStr("other-startup-key")})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(current_api_settings, (settings, other)))
    assert {item._api_settings_revision for item in results} == {0}
    assert len({item.llm_api_key.get_secret_value() for item in results}) == 1
    persisted = RuntimeSettingsStore(settings).resolve(0)
    assert all(item.llm_api_key == persisted.llm_api_key for item in results)


def test_missing_settings_volume_cannot_reinterpret_an_admitted_revision(settings):
    config = run_config(settings)
    RuntimeSettingsStore(settings).path.unlink()
    with pytest.raises(RuntimeSettingsError, match="固定"):
        settings_for_run(settings, config)


def test_revision_conflict_does_not_overwrite_and_only_one_writer_wins(settings):
    def save():
        try:
            return RuntimeSettingsStore(settings).save(update())["revision"]
        except RuntimeSettingsError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: save(), range(2)))
    assert sorted(map(str, outcomes)) == ["1", "settings_conflict"]
    assert RuntimeSettingsStore(settings).public()["revision"] == 1


def test_changing_endpoint_requires_a_new_key_and_clear_is_explicit(settings):
    store = RuntimeSettingsStore(settings)
    with pytest.raises(RuntimeSettingsError, match="重新填写"):
        store.save(update(llm={"base_url": "https://new.example/v1", "model": "x"}))
    result = store.save(
        update(llm={"base_url": "https://old.example/v1", "model": "x", "api_key": ""})
    )
    assert result["llm"]["key_configured"]
    result = store.save(
        update(1, llm={"base_url": "https://old.example/v1", "model": "x", "clear_api_key": True})
    )
    assert not result["llm"]["key_configured"]
    assert not current_api_settings(settings).llm_configured


def test_store_permissions_and_invalid_revision_fail_closed(settings):
    store = RuntimeSettingsStore(settings)
    store.save(update())
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(RuntimeSettingsError):
        settings_for_run(settings, {"api_settings_revision": 99})
    with pytest.raises(RuntimeSettingsError):
        settings_for_run(settings, {"api_settings_revision": True})


def test_vlm_can_explicitly_reuse_the_merged_text_key_only_at_the_same_endpoint(settings):
    store = RuntimeSettingsStore(settings)
    body = update(
        vlm={"base_url": "https://new.example/v1", "model": "new-text", "reuse_llm_key": True}
    )
    store.save(body)
    assert current_api_settings(settings).vlm_api_key.get_secret_value() == "new-text-secret"
    with pytest.raises(RuntimeSettingsError):
        store.save(
            update(
                1,
                vlm={
                    "base_url": "https://other.example/v1",
                    "model": "new-text",
                    "reuse_llm_key": True,
                },
            )
        )


def test_legacy_turn_stays_on_environment_settings_after_a_saved_change(settings):
    old_config = turn_config(settings)
    old_config.pop("api_settings_revision")
    RuntimeSettingsStore(settings).save(update())
    assert turn_config(settings_for_run(settings, old_config)) == old_config


def test_an_independent_process_loads_saved_and_historical_credentials(settings):
    RuntimeSettingsStore(settings).save(update())
    code = """
import sys
from pipe_inspector.config import Settings
from pipe_inspector.runtime_settings import RuntimeSettingsStore
store = RuntimeSettingsStore(Settings(_env_file=None, runtime_settings_dir=sys.argv[1]))
assert store.resolve().llm_api_key.get_secret_value() == 'new-text-secret'
assert store.resolve(0).vlm_api_key.get_secret_value() == 'old-vision-secret'
print(store.public()['revision'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(settings.runtime_settings_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "1"


def test_legacy_secret_urls_are_not_returned(settings):
    settings.llm_base_url = "https://legacy-secret@example.com/v1"
    response = RuntimeSettingsStore(settings).public()
    assert response["llm"]["base_url"] == ""
    assert not response["llm"]["configured"]
    assert "legacy-secret" not in json.dumps(response)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, repository=object())) as result:
        yield result


def test_api_protects_writes_and_never_echoes_keys(client):
    payload = update().model_dump(mode="json")
    payload["llm"]["api_key"] = "secret-not-for-response"
    assert client.put("/api/settings", json=payload).status_code == 403
    headers = {"X-Sewer-Settings": "1", "Origin": "https://evil.example"}
    assert client.put("/api/settings", json=payload, headers=headers).status_code == 403
    assert client.get("/api/settings", headers=headers).status_code == 403
    headers["Origin"] = "http://testserver"
    response = client.put("/api/settings", json=payload, headers=headers)
    assert response.status_code == 200
    assert "secret-not-for-response" not in response.text
    assert "secret-not-for-response" not in client.get("/api/settings").text
    payload["revision"] = 1
    payload["llm"]["base_url"] = "https://secret-not-for-response@example.com/v1"
    response = client.put("/api/settings", json=payload, headers=headers)
    assert response.status_code == 422
    assert "secret-not-for-response" not in response.text
    payload["llm"]["base_url"] = "https://new.example/v1"
    payload["llm"]["api_key"] = {"invalid": "secret-not-for-response"}
    response = client.put("/api/settings", json=payload, headers=headers)
    assert response.status_code == 422
    assert "secret-not-for-response" not in response.text


def test_api_conflict_and_same_origin_reverse_proxy(client):
    headers = {
        "X-Sewer-Settings": "1",
        "Host": "workbench.local",
        "Origin": "http://workbench.local",
    }
    payload = update().model_dump(mode="json")
    assert client.put("/api/settings", json=payload, headers=headers).status_code == 200
    response = client.put("/api/settings", json=payload, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "settings_conflict"
    assert client.get("/api/settings", headers=headers).json()["revision"] == 1


def test_api_directory_probe_is_read_only_and_does_not_follow_redirects(client, monkeypatch):
    import httpx
    from pipe_inspector.api import settings as settings_api

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "old-text"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        settings_api.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    response = client.post(
        "/api/settings/test",
        headers={"X-Sewer-Settings": "1"},
        json={
            "role": "llm",
            "base_url": "https://old.example/v1",
            "model": "old-text",
        },
    )
    assert response.status_code == 200
    assert response.json()["model_found"] is True
    assert len(requests) == 1 and requests[0].method == "GET"
    assert str(requests[0].url) == "https://old.example/v1/models"
    assert requests[0].headers["authorization"] == "Bearer old-text-secret"
    assert "secret" not in response.text
    assert client.get("/api/settings").json()["revision"] == 0


@pytest.mark.parametrize("status", [302, 401])
def test_probe_does_not_forward_keys_on_redirect_or_echo_provider_body(client, monkeypatch, status):
    import httpx
    from pipe_inspector.api import settings as settings_api

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="secret-provider-error",
            headers={"Location": "https://other.example/models"},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        settings_api.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    response = client.post(
        "/api/settings/test",
        headers={"X-Sewer-Settings": "1"},
        json={"role": "llm", "base_url": "https://old.example/v1", "model": "old-text"},
    )
    assert response.status_code == 200 and not response.json()["ok"]
    assert len(requests) == 1
    assert "secret" not in response.text
