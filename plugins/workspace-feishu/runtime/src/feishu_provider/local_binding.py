from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from typing import Protocol

from feishu_protocol import LocalClientIdentity


BINDING_SCHEMA_VERSION = 2
PROVIDER_ID = "feishu"
DEFAULT_BINDING_PARTS = (
    "WorkspaceCapabilities",
    "providers",
    "feishu",
    "binding.json",
)


class LocalIdentityError(RuntimeError):
    """Raised when the execution-client identity cannot be loaded safely."""


class SecretUnprotector(Protocol):
    def unprotect(self, protected_value: str) -> str: ...


class WindowsDpapiUnprotector:
    """Recover only the local execution-client secret for the current OS user."""

    def __init__(
        self,
        *,
        entropy: bytes = b"workspace-capabilities/feishu/v1",
    ) -> None:
        if os.name != "nt":
            raise LocalIdentityError("Windows DPAPI is only available on Windows")
        self._entropy = entropy

    def unprotect(self, protected_value: str) -> str:
        try:
            protected = base64.b64decode(
                protected_value.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise LocalIdentityError(
                "Protected local client secret is not valid base64"
            ) from exc
        plaintext = _crypt_unprotect(protected, self._entropy)
        try:
            value = plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LocalIdentityError(
                "Protected local client secret is not valid UTF-8"
            ) from exc
        if not value:
            raise LocalIdentityError("Protected local client secret is blank")
        return value


class LocalClientIdentityStore:
    """Read-only compatibility adapter for deployment binding schema v2."""

    def __init__(self, path: Path, unprotector: SecretUnprotector) -> None:
        self.path = path.resolve(strict=False)
        self._unprotector = unprotector

    @classmethod
    def default(cls) -> LocalClientIdentityStore:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise LocalIdentityError(
                "LOCALAPPDATA is required for the local Provider binding"
            )
        return cls(
            Path(local_app_data).joinpath(*DEFAULT_BINDING_PARTS),
            WindowsDpapiUnprotector(),
        )

    def load(self) -> LocalClientIdentity:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LocalIdentityError("Provider binding is not configured") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalIdentityError("Provider binding cannot be read") from exc
        if not isinstance(payload, dict):
            raise LocalIdentityError("Provider binding must be a JSON object")
        if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
            raise LocalIdentityError("Provider binding schema version is unsupported")
        if payload.get("provider_id") != PROVIDER_ID:
            raise LocalIdentityError("Provider binding identity is invalid")
        try:
            client_ref = _required_text("local client ref", payload["local_client_ref"])
            protected_secret = _required_text(
                "protected local client secret",
                payload["protected_local_client_secret"],
            )
        except KeyError as exc:
            raise LocalIdentityError(
                "Provider binding is missing local client identity"
            ) from exc
        return LocalClientIdentity(
            client_ref=client_ref,
            client_secret=self._unprotector.unprotect(protected_secret),
        )


def _required_text(label: str, value: object) -> str:
    if not isinstance(value, str):
        raise LocalIdentityError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise LocalIdentityError(f"{label} cannot be blank")
    if len(normalized) > 512 or any(ord(character) < 32 for character in normalized):
        raise LocalIdentityError(f"{label} contains unsupported characters")
    return normalized


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


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
        raise LocalIdentityError(
            f"Windows DPAPI unprotect failed: {ctypes.get_last_error()}"
        )
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
        raise LocalIdentityError("Windows DPAPI libraries are unavailable") from exc
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32
