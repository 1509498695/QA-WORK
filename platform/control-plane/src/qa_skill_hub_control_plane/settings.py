from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ControlPlaneConfigurationError(ValueError):
    """Raised when the development control-plane configuration is unsafe."""


@dataclass(frozen=True, slots=True)
class ControlPlaneSettings:
    database_path: Path
    public_origin: str = "http://127.0.0.1:3100"
    feishu_auth_origin: str = "http://127.0.0.1:3000"
    session_ttl_seconds: int = 43_200
    login_ttl_seconds: int = 300
    bootstrap_ttl_seconds: int = 900
    request_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_path", self.database_path.resolve(strict=False))
        _validate_local_origin("public_origin", self.public_origin)
        _validate_local_origin("feishu_auth_origin", self.feishu_auth_origin)
        if not 300 <= self.session_ttl_seconds <= 2_592_000:
            raise ControlPlaneConfigurationError(
                "session_ttl_seconds must be between 5 minutes and 30 days"
            )
        if not 30 <= self.login_ttl_seconds <= 900:
            raise ControlPlaneConfigurationError(
                "login_ttl_seconds must be between 30 and 900 seconds"
            )
        if not 60 <= self.bootstrap_ttl_seconds <= 3_600:
            raise ControlPlaneConfigurationError(
                "bootstrap_ttl_seconds must be between 60 and 3600 seconds"
            )
        if not 1 <= self.request_timeout_seconds <= 30:
            raise ControlPlaneConfigurationError(
                "request_timeout_seconds must be between 1 and 30 seconds"
            )

    @classmethod
    def default(cls) -> ControlPlaneSettings:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise ControlPlaneConfigurationError("LOCALAPPDATA is required")
        database_path = Path(local_app_data).joinpath(
            "WorkspaceCapabilities",
            "control-plane",
            "state-v1.sqlite3",
        )
        return cls.from_env(database_path=database_path)

    @classmethod
    def from_env(cls, *, database_path: Path | None = None) -> ControlPlaneSettings:
        if database_path is None:
            raw_path = os.getenv("QA_SKILL_HUB_STATE_PATH")
            if raw_path:
                database_path = Path(raw_path)
            else:
                local_app_data = os.getenv("LOCALAPPDATA")
                if not local_app_data:
                    raise ControlPlaneConfigurationError("LOCALAPPDATA is required")
                database_path = Path(local_app_data).joinpath(
                    "WorkspaceCapabilities",
                    "control-plane",
                    "state-v1.sqlite3",
                )
        return cls(
            database_path=database_path,
            public_origin=os.getenv(
                "QA_SKILL_HUB_PUBLIC_ORIGIN",
                "http://127.0.0.1:3100",
            ).rstrip("/"),
            feishu_auth_origin=os.getenv(
                "QA_SKILL_HUB_FEISHU_AUTH_ORIGIN",
                "http://127.0.0.1:3000",
            ).rstrip("/"),
            session_ttl_seconds=_read_int("QA_SKILL_HUB_SESSION_TTL_SECONDS", 43_200),
            login_ttl_seconds=_read_int("QA_SKILL_HUB_LOGIN_TTL_SECONDS", 300),
            bootstrap_ttl_seconds=_read_int("QA_SKILL_HUB_BOOTSTRAP_TTL_SECONDS", 900),
            request_timeout_seconds=_read_float(
                "QA_SKILL_HUB_REQUEST_TIMEOUT_SECONDS",
                5.0,
            ),
        )

    @property
    def callback_uri(self) -> str:
        return f"{self.public_origin}/auth/feishu/callback"

    @property
    def cookie_secure(self) -> bool:
        return urlsplit(self.public_origin).scheme == "https"


def _validate_local_origin(label: str, value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ControlPlaneConfigurationError(
            f"{label} must be an HTTP localhost origin without a path"
        )
    if parsed.port is None:
        raise ControlPlaneConfigurationError(f"{label} must include an explicit port")


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ControlPlaneConfigurationError(f"{name} must be an integer") from exc


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ControlPlaneConfigurationError(f"{name} must be a number") from exc
