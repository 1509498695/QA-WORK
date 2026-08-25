from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from feishu_auth_service.binding import SecretProtector, WindowsDpapiProtector


PROFILE_SCHEMA_VERSION = 1
PROVIDER_ID = "feishu"
DEFAULT_PROFILE_PARTS = ("WorkspaceCapabilities", "providers", "feishu", "profiles")
_PROFILE_REF = re.compile(r"^profile_[a-f0-9]{20}$")


class ProfileError(RuntimeError):
    """Raised when an encrypted local Provider Profile cannot be used safely."""


@dataclass(frozen=True, slots=True)
class ProfileAuthorization:
    profile_ref: str
    app_id: str
    tenant_key: str
    open_id: str
    union_id: str | None
    refresh_token: str
    refresh_token_expires_at: str | None
    scopes: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile_ref: str
    app_id: str
    tenant_key: str
    scopes: tuple[str, ...]
    refresh_token_configured: bool
    created_at: str
    updated_at: str


class ProfileVault(Protocol):
    def save_authorization(
        self,
        *,
        profile_ref: str,
        app_id: str,
        tenant_key: str,
        open_id: str,
        union_id: str | None,
        refresh_token: str,
        refresh_token_expires_in: int | None,
        scopes: tuple[str, ...],
    ) -> ProfileAuthorization: ...

    def load(self, profile_ref: str) -> ProfileAuthorization: ...

    def rotate_refresh_token(
        self,
        profile_ref: str,
        *,
        refresh_token: str,
        refresh_token_expires_in: int | None,
        scopes: tuple[str, ...],
    ) -> ProfileAuthorization: ...

    def summaries(self) -> tuple[ProfileSummary, ...]: ...

    def delete_all(self) -> None: ...


class LocalProfileVault:
    def __init__(self, root: Path, protector: SecretProtector) -> None:
        self.root = root.resolve(strict=False)
        self._protector = protector

    @classmethod
    def default(cls) -> LocalProfileVault:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise ProfileError("LOCALAPPDATA is required for Feishu Provider Profiles")
        return cls(
            Path(local_app_data).joinpath(*DEFAULT_PROFILE_PARTS),
            WindowsDpapiProtector(
                description="WorkspaceCapabilities Feishu Provider Profile",
                entropy=b"workspace-capabilities/feishu/profile/v1",
            ),
        )

    def save_authorization(
        self,
        *,
        profile_ref: str,
        app_id: str,
        tenant_key: str,
        open_id: str,
        union_id: str | None,
        refresh_token: str,
        refresh_token_expires_in: int | None,
        scopes: tuple[str, ...],
    ) -> ProfileAuthorization:
        profile_ref = _validate_profile_ref(profile_ref)
        app_id = _required_text("App ID", app_id)
        tenant_key = _required_text("tenant_key", tenant_key)
        open_id = _required_text("open_id", open_id)
        refresh_token = _required_text("refresh_token", refresh_token)
        normalized_scopes = _normalize_scopes(scopes)
        now = _utc_now()
        path = self._path(profile_ref)
        existing = self.load(profile_ref) if path.is_file() else None
        created_at = existing.created_at if existing is not None else now
        if existing is not None and (
            existing.app_id != app_id
            or existing.tenant_key != tenant_key
            or existing.open_id != open_id
        ):
            raise ProfileError("Profile identity does not match its existing authorization")
        refresh_expires_at = _future_timestamp(refresh_token_expires_in)
        payload = self._payload(
            profile_ref=profile_ref,
            app_id=app_id,
            tenant_key=tenant_key,
            open_id=open_id,
            union_id=union_id,
            refresh_token=refresh_token,
            refresh_token_expires_at=refresh_expires_at,
            scopes=normalized_scopes,
            created_at=created_at,
            updated_at=now,
        )
        self._atomic_write(path, payload)
        saved = self.load(profile_ref)
        _assert_authorization(saved, app_id, tenant_key, open_id, refresh_token)
        return saved

    def load(self, profile_ref: str) -> ProfileAuthorization:
        profile_ref = _validate_profile_ref(profile_ref)
        path = self._path(profile_ref)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProfileError("Provider Profile is not authorized") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProfileError("Provider Profile cannot be read") from exc
        if not isinstance(payload, dict):
            raise ProfileError("Provider Profile must be a JSON object")
        if payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
            raise ProfileError("Provider Profile schema version is unsupported")
        if payload.get("provider_id") != PROVIDER_ID:
            raise ProfileError("Provider Profile identity is invalid")
        try:
            stored_ref = _validate_profile_ref(payload["profile_ref"])
            app_id = _required_text("App ID", payload["app_id"])
            tenant_key = _required_text("tenant_key", payload["tenant_key"])
            scopes = _normalize_scopes(tuple(payload["scopes"]))
            protected_credentials = _required_text(
                "protected profile credentials",
                payload["protected_credentials"],
            )
            created_at = _required_text("created_at", payload["created_at"])
            updated_at = _required_text("updated_at", payload["updated_at"])
        except (KeyError, TypeError) as exc:
            raise ProfileError("Provider Profile is missing a required field") from exc
        if stored_ref != profile_ref:
            raise ProfileError("Provider Profile file identity does not match its content")
        try:
            credentials = json.loads(self._protector.unprotect(protected_credentials))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProfileError("Provider Profile credentials cannot be recovered") from exc
        if not isinstance(credentials, dict):
            raise ProfileError("Provider Profile credentials are invalid")
        try:
            open_id = _required_text("open_id", credentials["open_id"])
            refresh_token = _required_text("refresh_token", credentials["refresh_token"])
        except KeyError as exc:
            raise ProfileError("Provider Profile credentials are incomplete") from exc
        union_id = _optional_text(credentials.get("union_id"))
        refresh_expires_at = _optional_text(credentials.get("refresh_token_expires_at"))
        return ProfileAuthorization(
            profile_ref=profile_ref,
            app_id=app_id,
            tenant_key=tenant_key,
            open_id=open_id,
            union_id=union_id,
            refresh_token=refresh_token,
            refresh_token_expires_at=refresh_expires_at,
            scopes=scopes,
            created_at=created_at,
            updated_at=updated_at,
        )

    def rotate_refresh_token(
        self,
        profile_ref: str,
        *,
        refresh_token: str,
        refresh_token_expires_in: int | None,
        scopes: tuple[str, ...],
    ) -> ProfileAuthorization:
        current = self.load(profile_ref)
        return self.save_authorization(
            profile_ref=current.profile_ref,
            app_id=current.app_id,
            tenant_key=current.tenant_key,
            open_id=current.open_id,
            union_id=current.union_id,
            refresh_token=refresh_token,
            refresh_token_expires_in=refresh_token_expires_in,
            scopes=scopes or current.scopes,
        )

    def summaries(self) -> tuple[ProfileSummary, ...]:
        if not self.root.is_dir():
            return ()
        summaries = []
        for path in sorted(self.root.glob("profile_*.json")):
            profile = self.load(path.stem)
            summaries.append(
                ProfileSummary(
                    profile_ref=profile.profile_ref,
                    app_id=profile.app_id,
                    tenant_key=profile.tenant_key,
                    scopes=profile.scopes,
                    refresh_token_configured=bool(profile.refresh_token),
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                )
            )
        return tuple(summaries)

    def delete_all(self) -> None:
        if not self.root.is_dir():
            return
        for path in self.root.glob("profile_*.json"):
            path.unlink(missing_ok=True)
        if any(self.root.glob("profile_*.json")):
            raise ProfileError("Provider Profiles still exist after deletion")

    def _path(self, profile_ref: str) -> Path:
        return self.root / f"{_validate_profile_ref(profile_ref)}.json"

    def _payload(
        self,
        *,
        profile_ref: str,
        app_id: str,
        tenant_key: str,
        open_id: str,
        union_id: str | None,
        refresh_token: str,
        refresh_token_expires_at: str | None,
        scopes: tuple[str, ...],
        created_at: str,
        updated_at: str,
    ) -> bytes:
        credentials = json.dumps(
            {
                "open_id": open_id,
                "union_id": union_id,
                "refresh_token": refresh_token,
                "refresh_token_expires_at": refresh_token_expires_at,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "provider_id": PROVIDER_ID,
            "profile_ref": profile_ref,
            "app_id": app_id,
            "tenant_key": tenant_key,
            "scopes": list(scopes),
            "protected_credentials": self._protector.protect(credentials),
            "created_at": created_at,
            "updated_at": updated_at,
        }
        return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def _atomic_write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, raw_path = tempfile.mkstemp(
            prefix=".profile-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_path)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


def _validate_profile_ref(value: object) -> str:
    if not isinstance(value, str) or not _PROFILE_REF.fullmatch(value):
        raise ProfileError("Provider Profile reference is invalid")
    return value


def _required_text(label: str, value: object) -> str:
    if not isinstance(value, str):
        raise ProfileError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 4096:
        raise ProfileError(f"{label} is blank or too long")
    if any(ord(character) < 32 for character in normalized):
        raise ProfileError(f"{label} contains unsupported characters")
    return normalized


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError("optional profile field must be text")
    normalized = value.strip()
    if not normalized:
        return None
    return _required_text("optional profile field", normalized)


def _normalize_scopes(scopes: tuple[object, ...]) -> tuple[str, ...]:
    normalized = sorted({_required_text("scope", scope) for scope in scopes})
    if not normalized:
        raise ProfileError("Provider Profile must contain at least one Scope")
    return tuple(normalized)


def _future_timestamp(expires_in: int | None) -> str | None:
    if expires_in is None:
        return None
    if expires_in <= 0:
        raise ProfileError("Refresh Token lifetime must be positive")
    return (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(timespec="seconds")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _assert_authorization(
    profile: ProfileAuthorization,
    app_id: str,
    tenant_key: str,
    open_id: str,
    refresh_token: str,
) -> None:
    if (
        profile.app_id != app_id
        or profile.tenant_key != tenant_key
        or profile.open_id != open_id
        or profile.refresh_token != refresh_token
    ):
        raise ProfileError("Provider Profile readback does not match the authorization")
