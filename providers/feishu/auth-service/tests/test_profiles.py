from __future__ import annotations

import base64
from pathlib import Path

import pytest

from feishu_auth_service.profiles import LocalProfileVault, ProfileError


PROFILE_REF = "profile_0123456789abcdef0123"


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _vault(tmp_path: Path) -> LocalProfileVault:
    return LocalProfileVault(tmp_path / "profiles", FakeProtector())


def test_profile_vault_encrypts_rotates_reads_and_deletes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    created = vault.save_authorization(
        profile_ref=PROFILE_REF,
        app_id="cli_test",
        tenant_key="tenant-a",
        open_id="ou_sensitive_identity",
        union_id="on_sensitive_identity",
        refresh_token="first-refresh-token",
        refresh_token_expires_in=3600,
        scopes=("offline_access", "docx:document:readonly"),
    )

    serialized = next(vault.root.glob("*.json")).read_text(encoding="utf-8")
    assert "first-refresh-token" not in serialized
    assert "ou_sensitive_identity" not in serialized
    assert created.refresh_token == "first-refresh-token"
    assert vault.summaries()[0].refresh_token_configured is True

    rotated = vault.rotate_refresh_token(
        PROFILE_REF,
        refresh_token="rotated-refresh-token",
        refresh_token_expires_in=7200,
        scopes=("offline_access", "docx:document:readonly"),
    )

    assert rotated.created_at == created.created_at
    assert rotated.refresh_token == "rotated-refresh-token"
    assert "rotated-refresh-token" not in next(vault.root.glob("*.json")).read_text(
        encoding="utf-8"
    )

    vault.delete_all()

    assert vault.summaries() == ()
    with pytest.raises(ProfileError, match="not authorized"):
        vault.load(PROFILE_REF)


def test_profile_vault_rejects_identity_replacement_and_path_escape(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.save_authorization(
        profile_ref=PROFILE_REF,
        app_id="cli_test",
        tenant_key="tenant-a",
        open_id="ou_first",
        union_id=None,
        refresh_token="refresh-token",
        refresh_token_expires_in=None,
        scopes=("docx:document:readonly",),
    )

    with pytest.raises(ProfileError, match="identity"):
        vault.save_authorization(
            profile_ref=PROFILE_REF,
            app_id="cli_other",
            tenant_key="tenant-a",
            open_id="ou_first",
            union_id=None,
            refresh_token="refresh-token",
            refresh_token_expires_in=None,
            scopes=("docx:document:readonly",),
        )
    with pytest.raises(ProfileError, match="reference"):
        vault.load("../profile_escape")
