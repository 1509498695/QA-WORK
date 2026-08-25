from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from feishu_auth_service.config import Settings
from feishu_auth_service.models import TokenGrant, UserIdentity

AUTHORIZE_ENDPOINT = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TENANT_ACCESS_TOKEN_ENDPOINT = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
TENANT_QUERY_ENDPOINT = "https://open.feishu.cn/open-apis/tenant/v2/tenant/query"
TOKEN_ENDPOINT = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_ENDPOINT = "https://open.feishu.cn/open-apis/authen/v1/user_info"


class FeishuApiError(RuntimeError):
    def __init__(
        self,
        operation: str,
        *,
        http_status: int | None = None,
        platform_code: int | str | None = None,
    ) -> None:
        self.operation = operation
        self.http_status = http_status
        self.platform_code = platform_code
        super().__init__(f"Feishu {operation} failed")


class OAuthClient(Protocol):
    def authorization_url(self, state: str) -> str: ...

    async def exchange_code(self, code: str) -> TokenGrant: ...

    async def get_user_info(self, access_token: str) -> UserIdentity: ...

    async def refresh_access_token(self, refresh_token: str) -> TokenGrant: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    tenant_key: str
    name: str | None = None


class AppCredentialValidator(Protocol):
    async def resolve_tenant(
        self,
        app_id: str,
        app_secret: str,
    ) -> TenantIdentity: ...

    async def aclose(self) -> None: ...


class TokenRefresher(Protocol):
    async def refresh_access_token(self, refresh_token: str) -> TokenGrant: ...


class FeishuAppCredentialValidator:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    async def resolve_tenant(
        self,
        app_id: str,
        app_secret: str,
    ) -> TenantIdentity:
        try:
            response = await self._http.post(
                TENANT_ACCESS_TOKEN_ENDPOINT,
                json={"app_id": app_id, "app_secret": app_secret},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise FeishuApiError("app_credential_validation") from exc
        payload = _safe_json(response, "app_credential_validation")
        _ensure_success(response, payload, "app_credential_validation")
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            payload.clear()
            raise FeishuApiError(
                "app_credential_validation_contract",
                http_status=response.status_code,
            )
        payload.clear()
        try:
            tenant_response = await self._http.get(
                TENANT_QUERY_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            del token
            raise FeishuApiError("tenant_discovery") from exc
        del token
        tenant_payload = _safe_json(tenant_response, "tenant_discovery")
        _ensure_success(tenant_response, tenant_payload, "tenant_discovery")
        data = tenant_payload.get("data")
        tenant = data.get("tenant") if isinstance(data, dict) else None
        if not isinstance(tenant, dict):
            tenant_payload.clear()
            raise FeishuApiError(
                "tenant_discovery_contract",
                http_status=tenant_response.status_code,
            )
        tenant_key = _optional_text(tenant.get("tenant_key"))
        tenant_name = _optional_text(tenant.get("name"))
        tenant_payload.clear()
        if tenant_key is None:
            raise FeishuApiError(
                "tenant_discovery_contract",
                http_status=tenant_response.status_code,
            )
        return TenantIdentity(tenant_key=tenant_key, name=tenant_name)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()


class FeishuOAuthClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._settings.app_id,
                "redirect_uri": self._settings.redirect_uri,
                "state": state,
                "scope": " ".join(self._settings.scopes),
            }
        )
        return f"{AUTHORIZE_ENDPOINT}?{query}"

    async def exchange_code(self, code: str) -> TokenGrant:
        try:
            response = await self._http.post(
                TOKEN_ENDPOINT,
                json={
                    "grant_type": "authorization_code",
                    "client_id": self._settings.app_id,
                    "client_secret": self._settings.app_secret,
                    "code": code,
                    "redirect_uri": self._settings.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise FeishuApiError("token_exchange") from exc
        payload = _safe_json(response, "token_exchange")
        _ensure_success(response, payload, "token_exchange")
        grant = _parse_token_grant(payload, response, "token_exchange_contract")
        payload.clear()
        return grant

    async def refresh_access_token(self, refresh_token: str) -> TokenGrant:
        try:
            response = await self._http.post(
                TOKEN_ENDPOINT,
                json={
                    "grant_type": "refresh_token",
                    "client_id": self._settings.app_id,
                    "client_secret": self._settings.app_secret,
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise FeishuApiError("token_refresh") from exc
        payload = _safe_json(response, "token_refresh")
        _ensure_success(response, payload, "token_refresh")
        grant = _parse_token_grant(payload, response, "token_refresh_contract")
        payload.clear()
        return grant

    async def get_user_info(self, access_token: str) -> UserIdentity:
        try:
            response = await self._http.get(
                USER_INFO_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise FeishuApiError("user_info") from exc
        payload = _safe_json(response, "user_info")
        _ensure_success(response, payload, "user_info")
        data = payload.get("data")
        if not isinstance(data, dict):
            payload.clear()
            raise FeishuApiError("user_info_contract", http_status=response.status_code)
        try:
            identity = UserIdentity(
                tenant_key=str(data["tenant_key"]),
                open_id=str(data["open_id"]),
                union_id=_optional_text(data.get("union_id")),
                name=_optional_text(data.get("name")),
            )
        except KeyError as exc:
            payload.clear()
            raise FeishuApiError("user_info_contract", http_status=response.status_code) from exc
        payload.clear()
        if not identity.tenant_key or not identity.open_id:
            raise FeishuApiError("user_info_contract", http_status=response.status_code)
        return identity

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()


def _safe_json(response: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise FeishuApiError(operation, http_status=response.status_code) from exc
    if not isinstance(payload, dict):
        raise FeishuApiError(operation, http_status=response.status_code)
    return payload


def _ensure_success(
    response: httpx.Response,
    payload: dict[str, Any],
    operation: str,
) -> None:
    platform_code = payload.get("code")
    if response.is_success and platform_code in {0, "0", None}:
        return
    raise FeishuApiError(
        operation,
        http_status=response.status_code,
        platform_code=platform_code if isinstance(platform_code, (int, str)) else None,
    )


def _parse_token_grant(
    payload: dict[str, Any],
    response: httpx.Response,
    operation: str,
) -> TokenGrant:
    try:
        access_token = str(payload["access_token"])
        expires_in = int(payload["expires_in"])
        token_type = str(payload.get("token_type", "Bearer"))
        scopes = tuple(str(payload.get("scope", "")).replace(",", " ").split())
        refresh_token = _optional_text(payload.get("refresh_token"))
        raw_refresh_expires_in = payload.get("refresh_token_expires_in")
        refresh_token_expires_in = (
            int(raw_refresh_expires_in) if raw_refresh_expires_in is not None else None
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FeishuApiError(operation, http_status=response.status_code) from exc
    if not access_token or expires_in <= 0:
        raise FeishuApiError(operation, http_status=response.status_code)
    if refresh_token_expires_in is not None and refresh_token_expires_in <= 0:
        raise FeishuApiError(operation, http_status=response.status_code)
    return TokenGrant(
        access_token=access_token,
        expires_in=expires_in,
        scopes=scopes,
        token_type=token_type,
        refresh_token=refresh_token,
        refresh_token_expires_in=refresh_token_expires_in,
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
