from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_auth_service.app import create_app
from feishu_auth_service.config import Settings
from feishu_auth_service.leases import TokenLease
from feishu_auth_service.models import TokenGrant, UserIdentity
from feishu_auth_service.profiles import LocalProfileVault


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


class FakeOAuthClient:
    def __init__(
        self,
        tenant_key: str = "tenant-a",
        scopes: tuple[str, ...] = (
            "auth:user.id:read",
            "offline_access",
            "docx:document:readonly",
            "wiki:node:read",
            "docs:document.media:download",
            "sheets:spreadsheet:readonly",
        ),
    ) -> None:
        self.tenant_key = tenant_key
        self.scopes = scopes
        self.exchange_calls = 0
        self.closed = False

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.example/authorize?state={state}"

    async def exchange_code(self, code: str) -> TokenGrant:
        assert code == "one-time-code"
        self.exchange_calls += 1
        return TokenGrant(
            access_token="access-token-must-never-leak",
            expires_in=7200,
            scopes=self.scopes,
            token_type="Bearer",
            refresh_token="refresh-token-must-never-leak",
            refresh_token_expires_in=2592000,
        )

    async def get_user_info(self, access_token: str) -> UserIdentity:
        assert access_token == "access-token-must-never-leak"
        return UserIdentity(
            tenant_key=self.tenant_key,
            open_id="ou_1234567890abcdef",
            union_id="on_123",
            name="测试用户",
        )

    async def refresh_access_token(self, refresh_token: str) -> TokenGrant:
        raise AssertionError("Token refresh is not expected during OAuth callback tests")

    async def aclose(self) -> None:
        self.closed = True


class FakeLeaseBroker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.cleared = False

    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> TokenLease:
        self.calls.append((task_ref, profile_ref, capabilities))
        return TokenLease(
            lease_ref="lease_test",
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
            scopes=("docx:document:readonly",),
            access_token="leased-access-token-must-not-leak",
            issued_at="2026-08-24T00:00:00+00:00",
            expires_at="2026-08-24T00:10:00+00:00",
            token_expires_at="2026-08-24T01:00:00+00:00",
        )

    def clear(self) -> None:
        self.cleared = True


class AuthRequiredLeaseBroker(FakeLeaseBroker):
    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> TokenLease:
        self.calls.append((task_ref, profile_ref, capabilities))
        raise CapabilityError(
            CapabilityErrorCode.AUTH_REQUIRED,
            "The Profile needs an additional OAuth Scope.",
            details={"capabilities": ["feishu.sheets.read"]},
        )


def _vault(tmp_path: Path) -> LocalProfileVault:
    return LocalProfileVault(tmp_path / "profiles", FakeProtector())


def _start_state(client: TestClient) -> str:
    response = client.get("/oauth/start", follow_redirects=False)
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_verified_oauth_callback_is_single_use_and_redacted(tmp_path: Path) -> None:
    oauth = FakeOAuthClient()
    vault = _vault(tmp_path)
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="app-secret-must-never-leak",
            allowed_tenant_key="tenant-a",
        ),
        oauth,
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        callback = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"].startswith("/oauth/result/result_")

        result = client.get(callback.headers["location"])
        assert result.status_code == 200
        assert "verified" in result.text
        assert "profile_" in result.text
        assert "测试用户" in result.text

        combined = callback.text + result.text + str(result.headers)
        assert "access-token-must-never-leak" not in combined
        assert "refresh-token-must-never-leak" not in combined
        assert "app-secret-must-never-leak" not in combined
        assert "one-time-code" not in combined
        profile = vault.summaries()[0]
        assert profile.refresh_token_configured is True
        stored_profile = next(vault.root.glob("*.json")).read_text(encoding="utf-8")
        assert "refresh-token-must-never-leak" not in stored_profile

        replay = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )
        assert replay.status_code == 400
        assert "state_replayed" in replay.text
        assert oauth.exchange_calls == 1

    assert oauth.closed is True


def test_tenant_discovery_does_not_create_profile(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    app = create_app(
        Settings(app_id="cli_test", app_secret="secret"),
        FakeOAuthClient(),
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        callback = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
            follow_redirects=False,
        )
        result = client.get(callback.headers["location"])

        assert "tenant_discovered" in result.text
        assert "tenant-a" in result.text
        assert "profile_" not in result.text
        assert vault.summaries() == ()


def test_unapproved_tenant_is_rejected_without_result(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        FakeOAuthClient(tenant_key="tenant-b"),
        _vault(tmp_path),
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )

        assert response.status_code == 403
        assert "tenant_not_allowed" in response.text
        assert client.get("/status").json()["visible_results"] == 0


def test_user_denial_consumes_state_without_token_exchange(tmp_path: Path) -> None:
    oauth = FakeOAuthClient()
    app = create_app(
        Settings(app_id="cli_test", app_secret="secret"),
        oauth,
        _vault(tmp_path),
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "error": "access_denied"},
        )

        assert response.status_code == 400
        assert "oauth_denied" in response.text
        assert oauth.exchange_calls == 0


def test_oauth_without_confirmed_docx_scope_does_not_create_profile(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        FakeOAuthClient(scopes=("auth:user.id:read", "offline_access")),
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )

        assert response.status_code == 403
        assert "docx_scope_missing" in response.text
        assert vault.summaries() == ()


def test_oauth_without_confirmed_wiki_scope_does_not_create_profile(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        FakeOAuthClient(
            scopes=(
                "auth:user.id:read",
                "offline_access",
                "docx:document:readonly",
            )
        ),
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )

        assert response.status_code == 403
        assert "wiki_scope_missing" in response.text
        assert vault.summaries() == ()


def test_oauth_without_confirmed_docx_media_scope_does_not_create_profile(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        FakeOAuthClient(
            scopes=(
                "auth:user.id:read",
                "offline_access",
                "docx:document:readonly",
                "wiki:node:read",
            )
        ),
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )

        assert response.status_code == 403
        assert "docx_media_scope_missing" in response.text
        assert vault.summaries() == ()


def test_oauth_without_confirmed_sheets_scope_does_not_create_profile(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        FakeOAuthClient(
            scopes=(
                "auth:user.id:read",
                "offline_access",
                "docx:document:readonly",
                "wiki:node:read",
                "docs:document.media:download",
            )
        ),
        vault,
    )

    with TestClient(app) as client:
        state = _start_state(client)
        response = client.get(
            "/callback",
            params={"state": state, "code": "one-time-code"},
        )

        assert response.status_code == 403
        assert "sheets_scope_missing" in response.text
        assert vault.summaries() == ()


def test_security_headers_and_safe_status(tmp_path: Path) -> None:
    app = create_app(
        Settings(app_id="cli_test", app_secret="app-secret-must-never-leak"),
        FakeOAuthClient(),
        _vault(tmp_path),
    )

    with TestClient(app) as client:
        response = client.get("/status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["configuration"]["app_secret_configured"] is True
        assert payload["configuration"]["encrypted_refresh_token_profiles"] is True
        assert payload["authorized_profiles"] == 0
        assert "app-secret-must-never-leak" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_token_lease_endpoint_requires_local_client_and_returns_no_store(
    tmp_path: Path,
) -> None:
    oauth = FakeOAuthClient()
    broker = FakeLeaseBroker()
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="app-secret-must-never-leak",
            allowed_tenant_key="tenant-a",
            local_client_ref="client_test",
            local_client_secret="client-secret-must-never-leak",
        ),
        oauth,
        _vault(tmp_path),
        broker,  # type: ignore[arg-type]
    )
    payload = {
        "task_ref": "task-one",
        "profile_ref": "profile_0123456789abcdef0123",
        "capabilities": ["feishu.docx.read"],
    }

    with TestClient(app) as client:
        missing = client.post("/internal/v1/token-leases", json=payload)
        wrong = client.post(
            "/internal/v1/token-leases",
            json=payload,
            headers={
                "X-Workspace-Client-Ref": "client_test",
                "Authorization": "Bearer wrong-secret",
            },
        )
        issued = client.post(
            "/internal/v1/token-leases",
            json=payload,
            headers={
                "X-Workspace-Client-Ref": "client_test",
                "Authorization": "Bearer client-secret-must-never-leak",
            },
        )

        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert missing.json()["status"] == "client_unauthorized"
        assert "client-secret-must-never-leak" not in missing.text + wrong.text
        assert issued.status_code == 200
        assert issued.json()["access_token"] == "leased-access-token-must-not-leak"
        assert issued.headers["cache-control"] == "no-store"
        assert broker.calls == [
            (
                "task-one",
                "profile_0123456789abcdef0123",
                ("feishu.docx.read",),
            )
        ]

    assert broker.cleared is True
    assert oauth.closed is True


def test_token_lease_endpoint_fails_closed_when_client_is_not_configured(
    tmp_path: Path,
) -> None:
    broker = FakeLeaseBroker()
    app = create_app(
        Settings(app_id="cli_test", app_secret="secret"),
        FakeOAuthClient(),
        _vault(tmp_path),
        broker,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/token-leases",
            json={
                "task_ref": "task-one",
                "profile_ref": "profile_0123456789abcdef0123",
                "capabilities": ["feishu.docx.read"],
            },
        )

        assert response.status_code == 503
        assert response.json()["status"] == "configuration_required"
        assert broker.calls == []


def test_auth_required_lease_returns_clickable_authorization_url(
    tmp_path: Path,
) -> None:
    broker = AuthRequiredLeaseBroker()
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
            local_client_ref="client_test",
            local_client_secret="client-secret",
        ),
        FakeOAuthClient(),
        _vault(tmp_path),
        broker,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/token-leases",
            json={
                "task_ref": "task-one",
                "profile_ref": "profile_0123456789abcdef0123",
                "capabilities": ["feishu.sheets.read"],
            },
            headers={
                "X-Workspace-Client-Ref": "client_test",
                "Authorization": "Bearer client-secret",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "status": "auth_required",
        "message": "The Profile needs an additional OAuth Scope.",
        "retryable": False,
        "details": {
            "capabilities": ["feishu.sheets.read"],
            "authorization_url": "http://localhost:3000/oauth/start",
        },
    }


def test_token_lease_auto_selects_the_only_local_profile(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.save_authorization(
        profile_ref="profile_0123456789abcdef0123",
        app_id="cli_test",
        tenant_key="tenant-a",
        open_id="ou_1234567890abcdef",
        union_id=None,
        refresh_token="refresh-token-must-never-leak",
        refresh_token_expires_in=2592000,
        scopes=("sheets:spreadsheet:readonly",),
    )
    broker = FakeLeaseBroker()
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
            local_client_ref="client_test",
            local_client_secret="client-secret",
        ),
        FakeOAuthClient(),
        vault,
        broker,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/token-leases",
            json={
                "task_ref": "task-auto-profile",
                "capabilities": ["feishu.sheets.read"],
            },
            headers={
                "X-Workspace-Client-Ref": "client_test",
                "Authorization": "Bearer client-secret",
            },
        )

    assert response.status_code == 200
    assert response.json()["profile_ref"] == "profile_0123456789abcdef0123"
    assert broker.calls == [
        (
            "task-auto-profile",
            "profile_0123456789abcdef0123",
            ("feishu.sheets.read",),
        )
    ]


def test_token_lease_without_any_profile_returns_authorization_url(
    tmp_path: Path,
) -> None:
    broker = FakeLeaseBroker()
    app = create_app(
        Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
            local_client_ref="client_test",
            local_client_secret="client-secret",
        ),
        FakeOAuthClient(),
        _vault(tmp_path),
        broker,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/token-leases",
            json={
                "task_ref": "task-needs-auth",
                "capabilities": ["feishu.sheets.read"],
            },
            headers={
                "X-Workspace-Client-Ref": "client_test",
                "Authorization": "Bearer client-secret",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "status": "auth_required",
        "message": "No authorized Feishu Profile is available on this machine.",
        "retryable": False,
        "details": {
            "capabilities": ["feishu.sheets.read"],
            "authorization_url": "http://localhost:3000/oauth/start",
        },
    }
    assert broker.calls == []
