from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_auth_service.binding import LocalClientIdentity
from feishu_provider.lease_client import LoopbackLeaseClient


def _identity() -> LocalClientIdentity:
    return LocalClientIdentity(
        client_ref="client_test",
        client_secret="client-secret-must-never-render",
    )


def test_loopback_lease_client_authenticates_and_validates_delivery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:3000/internal/v1/token-leases"
        assert request.headers["x-workspace-client-ref"] == "client_test"
        assert request.headers["authorization"] == (
            "Bearer client-secret-must-never-render"
        )
        assert json.loads(request.content) == {
            "task_ref": "task-one",
            "profile_ref": "profile_0123456789abcdef0123",
            "capabilities": ["feishu.docx.read"],
        }
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "lease_ref": "lease_test",
                "task_ref": "task-one",
                "profile_ref": "profile_0123456789abcdef0123",
                "capabilities": ["feishu.docx.read"],
                "scopes": ["docx:document:readonly"],
                "access_token": "access-token-must-never-render",
                "issued_at": "2026-08-24T00:00:00+00:00",
                "expires_at": "2026-08-24T00:10:00+00:00",
                "token_expires_at": "2026-08-24T01:00:00+00:00",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LoopbackLeaseClient(identity=_identity(), http_client=http)
    lease = asyncio.run(
        client.issue(
            task_ref="task-one",
            profile_ref="profile_0123456789abcdef0123",
            capabilities=("feishu.docx.read",),
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert lease.lease_ref == "lease_test"
    assert lease.access_token == "access-token-must-never-render"
    assert "access-token-must-never-render" not in repr(lease)


def test_loopback_lease_client_omits_unselected_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "task_ref": "task-auto-profile",
            "capabilities": ["feishu.sheets.read"],
        }
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "lease_ref": "lease_auto",
                "task_ref": "task-auto-profile",
                "profile_ref": "profile_0123456789abcdef0123",
                "capabilities": ["feishu.sheets.read"],
                "scopes": ["sheets:spreadsheet:readonly"],
                "access_token": "access-token-must-never-render",
                "issued_at": "2026-08-24T00:00:00+00:00",
                "expires_at": "2026-08-24T00:10:00+00:00",
                "token_expires_at": "2026-08-24T01:00:00+00:00",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LoopbackLeaseClient(identity=_identity(), http_client=http)
    lease = asyncio.run(
        client.issue(
            task_ref="task-auto-profile",
            profile_ref=None,
            capabilities=("feishu.sheets.read",),
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert lease.profile_ref == "profile_0123456789abcdef0123"


def test_loopback_lease_client_maps_safe_control_plane_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "status": "client_unauthorized",
                "message": "upstream message must not be trusted",
                "retryable": False,
                "details": {"secret": "must-not-propagate"},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LoopbackLeaseClient(identity=_identity(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.issue(
                task_ref="task-one",
                profile_ref="profile_0123456789abcdef0123",
                capabilities=("feishu.docx.read",),
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.CLIENT_UNAUTHORIZED
    assert error.value.details == {}
    assert "must-not-propagate" not in str(error.value)


def test_loopback_lease_client_preserves_only_safe_auth_recovery_details() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "status": "auth_required",
                "message": "untrusted control-plane message",
                "retryable": False,
                "details": {
                    "capabilities": ["feishu.sheets.read"],
                    "authorization_url": "https://evil.example/steal",
                    "secret": "must-not-propagate",
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LoopbackLeaseClient(identity=_identity(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.issue(
                task_ref="task-one",
                profile_ref="profile_0123456789abcdef0123",
                capabilities=("feishu.sheets.read",),
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "authorization_url": "http://localhost:3000/oauth/start",
        "capabilities": ["feishu.sheets.read"],
    }
    assert "evil.example" not in str(error.value.details)
    assert "must-not-propagate" not in str(error.value.details)


def test_loopback_lease_client_rejects_non_loopback_origin() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        LoopbackLeaseClient(
            identity=_identity(),
            control_plane_origin="https://control.example",
        )
