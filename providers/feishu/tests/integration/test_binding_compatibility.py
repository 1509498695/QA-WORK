from __future__ import annotations

from pathlib import Path

from feishu_auth_service.binding import LocalBindingStore
from feishu_provider.local_binding import LocalClientIdentityStore


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return f"protected:{plaintext}"

    def unprotect(self, protected_value: str) -> str:
        prefix = "protected:"
        if not protected_value.startswith(prefix):
            raise ValueError("invalid protected value")
        return protected_value[len(prefix) :]


def test_mcp_reader_accepts_binding_written_by_auth_service(tmp_path: Path) -> None:
    path = tmp_path / "WorkspaceCapabilities" / "providers" / "feishu" / "binding.json"
    protector = FakeProtector()
    written = LocalBindingStore(path, protector).save(
        app_id="cli_test",
        app_secret="app-secret",
        allowed_tenant_key="tenant-a",
    )

    identity = LocalClientIdentityStore(path, protector).load()

    assert identity.client_ref == written.local_client_ref
    assert identity.client_secret == written.local_client_secret
