from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_protocol import LeaseDelivery, LocalClientIdentity
from feishu_provider.local_binding import LocalClientIdentityStore, LocalIdentityError


DEFAULT_CONTROL_PLANE_ORIGIN = "http://127.0.0.1:3000"
DEFAULT_AUTHORIZATION_URL = "http://localhost:3000/oauth/start"


@dataclass(frozen=True, slots=True)
class ProviderTokenLease:
    lease_ref: str
    task_ref: str
    profile_ref: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    access_token: str = field(repr=False)
    issued_at: str
    expires_at: str
    token_expires_at: str


class LeaseClient(Protocol):
    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str | None,
        capabilities: tuple[str, ...],
    ) -> ProviderTokenLease: ...

    async def aclose(self) -> None: ...


class LoopbackLeaseClient:
    def __init__(
        self,
        *,
        identity: LocalClientIdentity,
        control_plane_origin: str = DEFAULT_CONTROL_PLANE_ORIGIN,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_origin = urlsplit(control_plane_origin)
        if (
            parsed_origin.scheme != "http"
            or parsed_origin.hostname != "127.0.0.1"
            or parsed_origin.port is None
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError("Local-v0 lease client only accepts a 127.0.0.1 HTTP origin")
        self._identity = identity
        self._origin = control_plane_origin.rstrip("/")
        self._authorization_url = (
            f"http://localhost:{parsed_origin.port}/oauth/start"
        )
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    @classmethod
    def default(cls) -> LoopbackLeaseClient:
        try:
            identity = LocalClientIdentityStore.default().load()
        except LocalIdentityError as exc:
            raise CapabilityError(
                CapabilityErrorCode.CONFIGURATION_REQUIRED,
                "The Feishu Provider deployment binding is not configured.",
            ) from exc
        return cls(identity=identity)

    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str | None,
        capabilities: tuple[str, ...],
    ) -> ProviderTokenLease:
        try:
            payload: dict[str, object] = {
                "task_ref": task_ref,
                "capabilities": list(capabilities),
            }
            if profile_ref is not None:
                payload["profile_ref"] = profile_ref
            response = await self._http.post(
                f"{self._origin}/internal/v1/token-leases",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._identity.client_secret}",
                    "X-Workspace-Client-Ref": self._identity.client_ref,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                "The local Feishu authorization control plane is unavailable.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise _lease_error(response, self._authorization_url)
        try:
            delivery = LeaseDelivery.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "The authorization control plane returned an invalid lease contract.",
            ) from exc
        return ProviderTokenLease(
            lease_ref=delivery.lease_ref,
            task_ref=delivery.task_ref,
            profile_ref=delivery.profile_ref,
            capabilities=delivery.capabilities,
            scopes=delivery.scopes,
            access_token=delivery.access_token,
            issued_at=delivery.issued_at,
            expires_at=delivery.expires_at,
            token_expires_at=delivery.token_expires_at,
        )

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()


def _lease_error(
    response: httpx.Response,
    authorization_url: str,
) -> CapabilityError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    raw_status = payload.get("status") if isinstance(payload, dict) else None
    try:
        code = CapabilityErrorCode(raw_status)
    except (TypeError, ValueError):
        code = (
            CapabilityErrorCode.PROVIDER_UNAVAILABLE
            if response.status_code >= 500
            else CapabilityErrorCode.PROVIDER_CONTRACT_ERROR
        )
    retryable = bool(payload.get("retryable", False)) if isinstance(payload, dict) else False
    details: dict[str, object] = {}
    if code is CapabilityErrorCode.AUTH_REQUIRED:
        details["authorization_url"] = authorization_url
        raw_details = payload.get("details") if isinstance(payload, dict) else None
        raw_capabilities = (
            raw_details.get("capabilities") if isinstance(raw_details, dict) else None
        )
        if (
            isinstance(raw_capabilities, list)
            and len(raw_capabilities) <= 16
            and all(
                isinstance(capability, str)
                and 1 <= len(capability) <= 128
                and capability.strip() == capability
                for capability in raw_capabilities
            )
        ):
            details["capabilities"] = raw_capabilities
    return CapabilityError(
        code,
        "The local authorization control plane did not issue a task lease.",
        retryable=retryable,
        details=details,
    )
