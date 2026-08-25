from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from feishu_auth_service import cli
from feishu_auth_service.binding import BindingError, LocalBindingStore


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _store(tmp_path: Path, *, configured: bool = True) -> LocalBindingStore:
    store = LocalBindingStore(
        tmp_path / "WorkspaceCapabilities" / "providers" / "feishu" / "binding.json",
        FakeProtector(),
    )
    if configured:
        store.save(
            app_id="cli_local_binding",
            app_secret="never-print-this-secret",
            allowed_tenant_key="tenant-local",
        )
    return store


def _use_store(monkeypatch: pytest.MonkeyPatch, store: LocalBindingStore) -> None:
    monkeypatch.setattr(
        cli.LocalBindingStore,
        "default",
        classmethod(lambda _: store),
    )


def test_preflight_reads_local_binding_without_secret_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _store(tmp_path)
    _use_store(monkeypatch, store)

    assert cli.main(["preflight"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "ready"
    assert payload["configuration_source"] == "local_binding"
    assert payload["binding_path"] == str(store.path)
    assert "never-print-this-secret" not in output


def test_preflight_fails_closed_when_binding_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _store(tmp_path, configured=False)
    _use_store(monkeypatch, store)

    assert cli.main(["preflight"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "configuration_required"
    assert payload["secrets_in_output"] is False


def test_serve_uses_fixed_local_settings_and_no_environment_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _use_store(monkeypatch, store)
    captured: dict[str, object] = {}

    def fake_create_app(settings):  # type: ignore[no-untyped-def]
        captured["settings"] = settings
        return "test-app"

    def fake_run(app, **kwargs):  # type: ignore[no-untyped-def]
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    assert cli.main(["serve"]) == 0

    settings = captured["settings"]
    assert settings.configuration_source == "local_binding"  # type: ignore[union-attr]
    assert settings.allowed_tenant_key == "tenant-local"  # type: ignore[union-attr]
    assert captured["app"] == "test-app"
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 3000,
        "access_log": False,
        "log_level": "info",
        "proxy_headers": False,
        "server_header": False,
    }


def test_configure_refuses_while_normal_service_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _store(tmp_path, configured=False)
    _use_store(monkeypatch, store)
    monkeypatch.setattr(cli, "_normal_service_is_running", lambda: True)

    assert cli.main(["configure"]) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "normal_service_must_stop"
    assert payload["secrets_in_output"] is False


def test_configuration_process_lock_allows_only_one_writer(tmp_path: Path) -> None:
    lock_path = tmp_path / "admin-session.lock"

    with cli._configuration_process_lock(lock_path):
        with pytest.raises(BindingError, match="Another administrator"):
            with cli._configuration_process_lock(lock_path):
                pass
