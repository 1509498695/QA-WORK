from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from feishu_auth_service.binding import ProviderBinding


class ConfigurationError(ValueError):
    """Raised when the local OAuth service configuration is unsafe or incomplete."""


DEFAULT_OAUTH_SCOPES = (
    "auth:user.id:read",
    "offline_access",
    "docx:document:readonly",
    "wiki:node:read",
    "docs:document.media:download",
    "sheets:spreadsheet:readonly",
)


@dataclass(frozen=True, slots=True)
class Settings:
    app_id: str
    app_secret: str
    allowed_tenant_key: str | None = None
    redirect_uri: str = "http://localhost:3000/callback"
    scopes: tuple[str, ...] = DEFAULT_OAUTH_SCOPES
    host: str = "127.0.0.1"
    port: int = 3000
    state_ttl_seconds: int = 300
    result_ttl_seconds: int = 600
    request_timeout_seconds: float = 10.0
    configuration_source: str = "direct"
    local_client_ref: str | None = None
    local_client_secret: str | None = None

    def __post_init__(self) -> None:
        if not self.app_id.strip():
            raise ConfigurationError("FEISHU_APP_ID is required")
        if not self.app_secret.strip():
            raise ConfigurationError("FEISHU_APP_SECRET is required")
        if self.allowed_tenant_key is not None and not self.allowed_tenant_key.strip():
            raise ConfigurationError("FEISHU_ALLOWED_TENANT_KEY cannot be blank")

        parsed = urlsplit(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ConfigurationError(
                "The development slice only accepts an HTTP localhost redirect URI"
            )
        if parsed.query or parsed.fragment:
            raise ConfigurationError("FEISHU_REDIRECT_URI cannot contain query or fragment")
        if parsed.path != "/callback":
            raise ConfigurationError("FEISHU_REDIRECT_URI must use the exact /callback path")
        if self.host != "127.0.0.1":
            raise ConfigurationError("The development slice only binds to 127.0.0.1")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("FEISHU_AUTH_PORT must be between 1 and 65535")
        if parsed.port != self.port:
            raise ConfigurationError("FEISHU_REDIRECT_URI port must match FEISHU_AUTH_PORT")
        if not self.scopes:
            raise ConfigurationError("At least one OAuth scope is required")
        if (self.local_client_ref is None) != (self.local_client_secret is None):
            raise ConfigurationError(
                "Local execution client reference and secret must be configured together"
            )
        if self.local_client_ref is not None and not self.local_client_ref.strip():
            raise ConfigurationError("Local execution client reference cannot be blank")
        if self.local_client_secret is not None and not self.local_client_secret.strip():
            raise ConfigurationError("Local execution client secret cannot be blank")
        if not 30 <= self.state_ttl_seconds <= 900:
            raise ConfigurationError("State TTL must be between 30 and 900 seconds")
        if not 30 <= self.result_ttl_seconds <= 1800:
            raise ConfigurationError("Result TTL must be between 30 and 1800 seconds")
        if not 1 <= self.request_timeout_seconds <= 60:
            raise ConfigurationError("Request timeout must be between 1 and 60 seconds")
        if self.configuration_source not in {"direct", "environment", "local_binding"}:
            raise ConfigurationError("Configuration source is invalid")

    @classmethod
    def from_env(cls) -> Settings:
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        allowed_tenant = os.getenv("FEISHU_ALLOWED_TENANT_KEY")
        redirect_uri = os.getenv("FEISHU_REDIRECT_URI", "http://localhost:3000/callback")
        scopes = tuple(
            item
            for item in os.getenv(
                "FEISHU_SCOPES",
                " ".join(DEFAULT_OAUTH_SCOPES),
            ).split()
            if item
        )
        host = os.getenv("FEISHU_AUTH_HOST", "127.0.0.1")
        port = _read_int("FEISHU_AUTH_PORT", 3000)
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            allowed_tenant_key=allowed_tenant,
            redirect_uri=redirect_uri,
            scopes=scopes,
            host=host,
            port=port,
            state_ttl_seconds=_read_int("FEISHU_STATE_TTL_SECONDS", 300),
            result_ttl_seconds=_read_int("FEISHU_RESULT_TTL_SECONDS", 600),
            request_timeout_seconds=_read_float("FEISHU_REQUEST_TIMEOUT_SECONDS", 10.0),
            configuration_source="environment",
        )

    @classmethod
    def from_binding(cls, binding: ProviderBinding) -> Settings:
        return cls(
            app_id=binding.app_id,
            app_secret=binding.app_secret,
            allowed_tenant_key=binding.allowed_tenant_key,
            configuration_source="local_binding",
            local_client_ref=binding.local_client_ref,
            local_client_secret=binding.local_client_secret,
        )

    def safe_status(self) -> dict[str, object]:
        return {
            "app_id_configured": bool(self.app_id),
            "app_secret_configured": bool(self.app_secret),
            "allowed_tenant_configured": self.allowed_tenant_key is not None,
            "redirect_uri": self.redirect_uri,
            "authorization_url": self.authorization_url,
            "scopes": list(self.scopes),
            "persistent_tokens": False,
            "persistent_access_tokens": False,
            "encrypted_refresh_token_profiles": True,
            "local_execution_client_configured": self.local_client_ref is not None,
            "development_only": True,
            "configuration_source": self.configuration_source,
        }

    @property
    def authorization_url(self) -> str:
        return f"http://localhost:{self.port}/oauth/start"


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
