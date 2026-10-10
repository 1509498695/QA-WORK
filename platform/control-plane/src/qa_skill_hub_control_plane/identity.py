from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

import httpx

from qa_skill_hub_control_plane.models import ProviderStatus, VerifiedIdentity
from qa_skill_hub_control_plane.settings import ControlPlaneSettings


class IdentityGatewayError(RuntimeError):
    """Raised when a verified Provider identity cannot be obtained safely."""


class IdentityGateway(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        return_to: str,
    ) -> str: ...

    async def redeem(self, *, ticket: str, code_verifier: str) -> VerifiedIdentity: ...

    async def status(self) -> ProviderStatus: ...


class FeishuIdentityGateway:
    """Thin HTTP adapter; all OAuth credentials remain inside auth-service."""

    def __init__(self, settings: ControlPlaneSettings) -> None:
        self._origin = settings.feishu_auth_origin
        self._timeout = settings.request_timeout_seconds

    def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        return_to: str,
    ) -> str:
        query = urlencode(
            {
                "state": state,
                "code_challenge": code_challenge,
                "return_to": return_to,
            }
        )
        return f"{self._origin}/platform/login/start?{query}"

    async def redeem(self, *, ticket: str, code_verifier: str) -> VerifiedIdentity:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._origin}/internal/v1/platform-identities/redeem",
                    json={"ticket": ticket, "code_verifier": code_verifier},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise IdentityGatewayError("飞书身份服务当前不可达") from exc
        if response.status_code != 200:
            raise IdentityGatewayError("飞书身份票据无效、已过期或已使用")
        try:
            payload = response.json()
            identity = payload["identity"]
            if payload.get("status") != "verified" or not isinstance(identity, dict):
                raise KeyError("identity")
            return VerifiedIdentity(
                app_id=_required_text(identity, "app_id"),
                tenant_ref=_required_text(identity, "tenant_key"),
                open_id=_required_text(identity, "open_id"),
                union_id=_optional_text(identity.get("union_id")),
                verified_name=_optional_text(identity.get("name")),
                avatar_url=_optional_text(identity.get("avatar_url")),
                profile_ref=_required_text(identity, "profile_ref"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityGatewayError("飞书身份服务返回了无法识别的结果") from exc

    async def status(self) -> ProviderStatus:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._origin}/status",
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            payload = response.json()
            configuration = payload.get("configuration", {})
            if not isinstance(configuration, dict):
                raise ValueError("configuration")
            configured = bool(
                configuration.get("app_id_configured")
                and configuration.get("app_secret_configured")
                and configuration.get("allowed_tenant_configured")
            )
            return ProviderStatus(
                reachable=True,
                configured=configured,
                app_id=_optional_text(configuration.get("app_id")),
                tenant_ref=_optional_text(configuration.get("allowed_tenant_key")),
                authorized_profiles=int(payload.get("authorized_profiles", 0)),
                callback_uri=_optional_text(configuration.get("redirect_uri")),
            )
        except (httpx.HTTPError, TypeError, ValueError):
            return ProviderStatus(
                reachable=False,
                configured=False,
                message="飞书 OAuth 服务不可达",
            )


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    normalized = _optional_text(value)
    if normalized is None:
        raise ValueError(key)
    return normalized


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("text")
    normalized = value.strip()
    return normalized or None
