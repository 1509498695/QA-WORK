from __future__ import annotations

import base64
import ctypes
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


BINDING_SCHEMA_VERSION = 2
PROVIDER_ID = "feishu"
DEFAULT_BINDING_PARTS = ("WorkspaceCapabilities", "providers", "feishu", "binding.json")


class BindingError(RuntimeError):
    """Raised when a local Provider binding cannot be safely loaded or changed."""


class SecretProtectionError(BindingError):
    """Raised when the operating system cannot protect or recover a secret."""


class SecretProtector(Protocol):
    def protect(self, plaintext: str) -> str: ...

    def unprotect(self, protected_value: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    app_id: str
    app_secret: str
    allowed_tenant_key: str
    local_client_ref: str
    local_client_secret: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class BindingSummary:
    path: Path
    app_id: str
    allowed_tenant_key: str
    secret_configured: bool
    local_client_ref: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LocalClientIdentity:
    client_ref: str
    client_secret: str


class WindowsDpapiProtector:
    """Protect secrets with Windows DPAPI for the current OS user."""

    def __init__(
        self,
        *,
        description: str = "WorkspaceCapabilities Feishu Provider Secrets",
        entropy: bytes = b"workspace-capabilities/feishu/v1",
    ) -> None:
        if os.name != "nt":
            raise SecretProtectionError("Windows DPAPI is only available on Windows")
        self._description = description
        self._entropy = entropy

    def protect(self, plaintext: str) -> str:
        if not plaintext:
            raise SecretProtectionError("App Secret cannot be blank")
        protected = _crypt_protect(
            plaintext.encode("utf-8"),
            self._entropy,
            self._description,
        )
        return base64.urlsafe_b64encode(protected).decode("ascii")

    def unprotect(self, protected_value: str) -> str:
        try:
            protected = base64.b64decode(protected_value.encode("ascii"), altchars=b"-_", validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise SecretProtectionError("Protected App Secret is not valid base64") from exc
        plaintext = _crypt_unprotect(protected, self._entropy)
        try:
            value = plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretProtectionError("Protected App Secret is not valid UTF-8") from exc
        if not value:
            raise SecretProtectionError("Protected App Secret is blank")
        return value


class LocalBindingStore:
    def __init__(self, path: Path, protector: SecretProtector) -> None:
        self.path = path.resolve(strict=False)
        self._protector = protector

    @classmethod
    def default(cls) -> LocalBindingStore:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise BindingError("LOCALAPPDATA is required for the local Provider binding")
        return cls(Path(local_app_data).joinpath(*DEFAULT_BINDING_PARTS), WindowsDpapiProtector())

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> ProviderBinding:
        return self._load_path(self.path)

    def load_client_identity(self) -> LocalClientIdentity:
        binding = self.load()
        return LocalClientIdentity(
            client_ref=binding.local_client_ref,
            client_secret=binding.local_client_secret,
        )

    def summary(self) -> BindingSummary | None:
        if not self.exists():
            return None
        binding = self.load()
        return BindingSummary(
            path=self.path,
            app_id=binding.app_id,
            allowed_tenant_key=binding.allowed_tenant_key,
            secret_configured=bool(binding.app_secret),
            local_client_ref=binding.local_client_ref,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )

    def save(self, *, app_id: str, app_secret: str, allowed_tenant_key: str) -> ProviderBinding:
        app_id, app_secret, allowed_tenant_key = validate_binding_values(
            app_id,
            app_secret,
            allowed_tenant_key,
        )
        now = _utc_now()
        existing = self.load() if self.exists() else None
        created_at = existing.created_at if existing is not None else now
        local_client_ref = (
            existing.local_client_ref
            if existing is not None
            else f"client_{secrets.token_hex(10)}"
        )
        local_client_secret = (
            existing.local_client_secret
            if existing is not None
            else secrets.token_urlsafe(32)
        )
        payload = {
            "schema_version": BINDING_SCHEMA_VERSION,
            "provider_id": PROVIDER_ID,
            "app_id": app_id,
            "allowed_tenant_key": allowed_tenant_key,
            "protected_app_secret": self._protector.protect(app_secret),
            "local_client_ref": local_client_ref,
            "protected_local_client_secret": self._protector.protect(local_client_secret),
            "created_at": created_at,
            "updated_at": now,
        }
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = self.path.read_bytes() if self.exists() else None
        temp_path = self._write_temp(serialized)
        try:
            staged = self._load_path(temp_path)
            _assert_same_binding(
                staged,
                app_id,
                app_secret,
                allowed_tenant_key,
                local_client_ref,
                local_client_secret,
            )
            os.replace(temp_path, self.path)
            saved = self.load()
            _assert_same_binding(
                saved,
                app_id,
                app_secret,
                allowed_tenant_key,
                local_client_ref,
                local_client_secret,
            )
            return saved
        except Exception:
            temp_path.unlink(missing_ok=True)
            if previous is not None and not _bytes_match(self.path, previous):
                rollback_path = self._write_temp(previous)
                os.replace(rollback_path, self.path)
            elif previous is None and self.path.exists():
                self.path.unlink(missing_ok=True)
            raise

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        if self.path.exists():
            raise BindingError("Provider binding still exists after deletion")

    def _write_temp(self, content: bytes) -> Path:
        file_descriptor, raw_path = tempfile.mkstemp(
            prefix=".binding-",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temp_path = Path(raw_path)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return temp_path

    def _load_path(self, path: Path) -> ProviderBinding:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BindingError("Provider binding is not configured") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BindingError("Provider binding cannot be read") from exc
        if not isinstance(payload, dict):
            raise BindingError("Provider binding must be a JSON object")
        if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
            raise BindingError("Provider binding schema version is unsupported")
        if payload.get("provider_id") != PROVIDER_ID:
            raise BindingError("Provider binding identity is invalid")
        try:
            app_id = _required_text("App ID", payload["app_id"])
            tenant_key = _required_text("tenant_key", payload["allowed_tenant_key"])
            protected_secret = _required_text(
                "protected App Secret", payload["protected_app_secret"]
            )
            local_client_ref = _required_text(
                "local client ref", payload["local_client_ref"]
            )
            protected_local_client_secret = _required_text(
                "protected local client secret",
                payload["protected_local_client_secret"],
            )
            created_at = _required_text("created_at", payload["created_at"])
            updated_at = _required_text("updated_at", payload["updated_at"])
        except KeyError as exc:
            raise BindingError("Provider binding is missing a required field") from exc
        app_secret = self._protector.unprotect(protected_secret)
        local_client_secret = self._protector.unprotect(protected_local_client_secret)
        return ProviderBinding(
            app_id=app_id,
            app_secret=app_secret,
            allowed_tenant_key=tenant_key,
            local_client_ref=local_client_ref,
            local_client_secret=local_client_secret,
            created_at=created_at,
            updated_at=updated_at,
        )


def _required_text(label: str, value: object) -> str:
    if not isinstance(value, str):
        raise BindingError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise BindingError(f"{label} cannot be blank")
    if len(normalized) > 512 or any(ord(character) < 32 for character in normalized):
        raise BindingError(f"{label} contains unsupported characters")
    return normalized


def validate_binding_values(
    app_id: object,
    app_secret: object,
    allowed_tenant_key: object,
) -> tuple[str, str, str]:
    normalized_app_id, normalized_app_secret = validate_application_credentials(
        app_id,
        app_secret,
    )
    return (
        normalized_app_id,
        normalized_app_secret,
        _required_text("tenant_key", allowed_tenant_key),
    )


def validate_application_credentials(
    app_id: object,
    app_secret: object,
) -> tuple[str, str]:
    return (
        _required_text("App ID", app_id),
        _required_text("App Secret", app_secret),
    )


def _assert_same_binding(
    binding: ProviderBinding,
    app_id: str,
    app_secret: str,
    allowed_tenant_key: str,
    local_client_ref: str,
    local_client_secret: str,
) -> None:
    if (
        binding.app_id != app_id
        or binding.allowed_tenant_key != allowed_tenant_key
        or binding.local_client_ref != local_client_ref
    ):
        raise BindingError("Provider binding readback does not match the requested identity")
    if not hmac.compare_digest(binding.app_secret, app_secret):
        raise BindingError("Provider binding App Secret readback does not match")
    if not hmac.compare_digest(binding.local_client_secret, local_client_secret):
        raise BindingError("Provider binding local client readback does not match")


def _bytes_match(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


def _crypt_protect(value: bytes, entropy: bytes, description: str) -> bytes:
    crypt32, kernel32 = _windows_crypto_libraries()
    input_blob, input_buffer = _input_blob(value)
    entropy_blob, entropy_buffer = _input_blob(entropy)
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        description,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise SecretProtectionError(f"Windows DPAPI protect failed: {ctypes.get_last_error()}")
    try:
        del input_buffer, entropy_buffer
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def _crypt_unprotect(value: bytes, entropy: bytes) -> bytes:
    crypt32, kernel32 = _windows_crypto_libraries()
    input_blob, input_buffer = _input_blob(value)
    entropy_blob, entropy_buffer = _input_blob(entropy)
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise SecretProtectionError(f"Windows DPAPI unprotect failed: {ctypes.get_last_error()}")
    try:
        del input_buffer, entropy_buffer
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def _windows_crypto_libraries():  # type: ignore[no-untyped-def]
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise SecretProtectionError("Windows DPAPI libraries are unavailable") from exc
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32
