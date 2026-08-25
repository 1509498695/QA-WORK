from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from feishu_auth_service.binding import (
    BindingError,
    LocalBindingStore,
    WindowsDpapiProtector,
)


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _store(tmp_path: Path) -> LocalBindingStore:
    return LocalBindingStore(tmp_path / "provider" / "binding.json", FakeProtector())


def test_binding_store_creates_updates_reads_and_deletes_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.save(
        app_id="cli_first",
        app_secret="first-secret-value",
        allowed_tenant_key="tenant-a",
    )

    assert store.exists() is True
    assert created.app_id == "cli_first"
    assert created.app_secret == "first-secret-value"
    assert created.local_client_ref.startswith("client_")
    assert created.local_client_secret
    serialized = store.path.read_text(encoding="utf-8")
    assert "first-secret-value" not in serialized
    assert created.local_client_secret not in serialized

    updated = store.save(
        app_id="cli_second",
        app_secret="second-secret-value",
        allowed_tenant_key="tenant-b",
    )

    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert store.load().app_secret == "second-secret-value"
    assert updated.local_client_ref == created.local_client_ref
    assert updated.local_client_secret == created.local_client_secret
    assert store.load_client_identity().client_ref == created.local_client_ref
    assert not list(store.path.parent.glob(".binding-*.tmp"))

    store.delete()

    assert store.exists() is False
    with pytest.raises(BindingError, match="not configured"):
        store.load()


def test_binding_store_rejects_partial_or_foreign_bindings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{"schema_version": 2, "provider_id": "svn"}', encoding="utf-8")

    with pytest.raises(BindingError, match="identity"):
        store.load()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is required")
def test_windows_dpapi_round_trip_does_not_store_plaintext() -> None:
    protector = WindowsDpapiProtector()
    plaintext = "dpapi-round-trip-test-value"

    protected = protector.protect(plaintext)

    assert plaintext not in protected
    assert protector.unprotect(protected) == plaintext
