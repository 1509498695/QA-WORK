from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
from feishu_auth_service.config import Settings
from feishu_auth_service.feishu import (
    AUTHORIZE_ENDPOINT,
    TENANT_ACCESS_TOKEN_ENDPOINT,
    TENANT_QUERY_ENDPOINT,
    TOKEN_ENDPOINT,
    USER_INFO_ENDPOINT,
    FeishuAppCredentialValidator,
    FeishuOAuthClient,
)


def test_feishu_client_uses_current_oauth_contract() -> None:
    asyncio.run(_exercise_feishu_client())


async def _exercise_feishu_client() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == TOKEN_ENDPOINT:
            body = json.loads(request.content)
            assert body["client_secret"] == "secret-value"
            if body["grant_type"] == "authorization_code":
                assert body["redirect_uri"] == "http://localhost:3000/callback"
                assert body["code"] == "one-time-code"
                access_token = "access-secret"
                refresh_token = "first-refresh-token"
            else:
                assert body == {
                    "grant_type": "refresh_token",
                    "client_id": "cli_test",
                    "client_secret": "secret-value",
                    "refresh_token": "first-refresh-token",
                }
                access_token = "refreshed-access-secret"
                refresh_token = "rotated-refresh-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": access_token,
                    "expires_in": 7200,
                    "refresh_token": refresh_token,
                    "refresh_token_expires_in": 2592000,
                    "scope": (
                        "auth:user.id:read offline_access "
                        "docx:document:readonly wiki:node:read "
                        "docs:document.media:download "
                        "sheets:spreadsheet:readonly"
                    ),
                    "token_type": "Bearer",
                },
            )
        assert str(request.url) == USER_INFO_ENDPOINT
        assert request.headers["Authorization"] == "Bearer access-secret"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "tenant_key": "tenant-a",
                    "open_id": "ou_1234567890",
                    "union_id": "on_123",
                    "name": "Test User",
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuOAuthClient(
        Settings(app_id="cli_test", app_secret="secret-value"),
        http_client=http_client,
    )
    url = client.authorization_url("state-value")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE_ENDPOINT
    assert query == {
        "client_id": ["cli_test"],
        "redirect_uri": ["http://localhost:3000/callback"],
        "scope": [
            "auth:user.id:read offline_access "
                "docx:document:readonly wiki:node:read "
                "docs:document.media:download "
                "sheets:spreadsheet:readonly"
        ],
        "state": ["state-value"],
    }

    grant = await client.exchange_code("one-time-code")
    identity = await client.get_user_info(grant.access_token)
    refreshed = await client.refresh_access_token(grant.refresh_token or "")

    assert grant.expires_in == 7200
    assert identity.tenant_key == "tenant-a"
    assert identity.open_id == "ou_1234567890"
    assert grant.refresh_token == "first-refresh-token"
    assert refreshed.access_token == "refreshed-access-secret"
    assert refreshed.refresh_token == "rotated-refresh-token"
    assert len(seen) == 3
    await http_client.aclose()


def test_app_credential_validator_discovers_tenant_and_discards_token() -> None:
    asyncio.run(_exercise_app_credential_validator())


async def _exercise_app_credential_validator() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TENANT_ACCESS_TOKEN_ENDPOINT:
            body = json.loads(request.content)
            assert body == {"app_id": "cli_test", "app_secret": "secret-value"}
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "validation-token-must-not-escape",
                    "expire": 7200,
                },
            )
        assert str(request.url) == TENANT_QUERY_ENDPOINT
        assert request.headers["authorization"] == (
            "Bearer validation-token-must-not-escape"
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "tenant": {
                        "tenant_key": "tenant-a",
                        "name": "测试企业",
                    }
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    validator = FeishuAppCredentialValidator(http_client=http_client)

    tenant = await validator.resolve_tenant("cli_test", "secret-value")

    assert tenant.tenant_key == "tenant-a"
    assert tenant.name == "测试企业"
    assert "validation-token-must-not-escape" not in repr(tenant)
    await http_client.aclose()
