from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from feishu_auth_service.app import create_app
from feishu_auth_service.config import Settings
from feishu_auth_service.leases import TokenLease
from feishu_protocol import DOCX_READ_CAPABILITY, LocalClientIdentity
from feishu_provider.lease_client import LoopbackLeaseClient


PROFILE_REF = "profile_0123456789abcdef0123"


class FakeOAuthClient:
    async def aclose(self) -> None:
        return None


class FakeProfileVault:
    def summaries(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(profile_ref=PROFILE_REF)]


class FakeLeaseBroker:
    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> TokenLease:
        return TokenLease(
            lease_ref="lease_roundtrip",
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
            scopes=("docx:document:readonly",),
            access_token="leased-access-token",
            issued_at="2026-08-25T00:00:00+00:00",
            expires_at="2026-08-25T00:10:00+00:00",
            token_expires_at="2026-08-25T01:00:00+00:00",
        )

    def clear(self) -> None:
        return None


def test_auth_endpoint_and_mcp_lease_client_share_the_protocol() -> None:
    async def exercise():
        app = create_app(
            Settings(
                app_id="cli_test",
                app_secret="app-secret",
                allowed_tenant_key="tenant-a",
                local_client_ref="client_test",
                local_client_secret="client-secret",
            ),
            oauth_client=FakeOAuthClient(),  # type: ignore[arg-type]
            profile_vault=FakeProfileVault(),  # type: ignore[arg-type]
            lease_broker=FakeLeaseBroker(),  # type: ignore[arg-type]
        )
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        client = LoopbackLeaseClient(
            identity=LocalClientIdentity(
                client_ref="client_test",
                client_secret="client-secret",
            ),
            http_client=http,
        )
        try:
            return await client.issue(
                task_ref="task-roundtrip",
                profile_ref=None,
                capabilities=(DOCX_READ_CAPABILITY,),
            )
        finally:
            await client.aclose()
            await http.aclose()

    lease = asyncio.run(exercise())

    assert lease.lease_ref == "lease_roundtrip"
    assert lease.profile_ref == PROFILE_REF
    assert lease.capabilities == (DOCX_READ_CAPABILITY,)
    assert lease.access_token == "leased-access-token"
