from __future__ import annotations

import json
from pathlib import Path

import pytest

from feishu_provider.local_binding import LocalClientIdentityStore, LocalIdentityError


class FakeUnprotector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def unprotect(self, protected_value: str) -> str:
        self.calls.append(protected_value)
        return "local-client-secret"


def _write_binding(path: Path, *, schema_version: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "provider_id": "feishu",
                "app_id": "cli_test",
                "allowed_tenant_key": "tenant-a",
                "protected_app_secret": "must-not-be-read",
                "local_client_ref": "client_0123456789abcdef0123",
                "protected_local_client_secret": "protected-local-client-secret",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_read_only_store_loads_only_execution_client_identity(tmp_path: Path) -> None:
    path = tmp_path / "WorkspaceCapabilities" / "providers" / "feishu" / "binding.json"
    _write_binding(path)
    unprotector = FakeUnprotector()

    identity = LocalClientIdentityStore(path, unprotector).load()

    assert identity.client_ref == "client_0123456789abcdef0123"
    assert identity.client_secret == "local-client-secret"
    assert unprotector.calls == ["protected-local-client-secret"]


def test_read_only_store_rejects_unknown_binding_schema(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    _write_binding(path, schema_version=1)

    with pytest.raises(LocalIdentityError, match="schema version"):
        LocalClientIdentityStore(path, FakeUnprotector()).load()
