from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from qa_skill_hub_control_plane.app import create_app
from qa_skill_hub_control_plane.models import ProviderStatus, VerifiedIdentity
from qa_skill_hub_control_plane.pages import Viewer, visible_navigation
from qa_skill_hub_control_plane.settings import ControlPlaneSettings
from qa_skill_hub_control_plane.store import ControlPlaneStore


ORIGIN = "http://127.0.0.1:3100"


class FakeIdentityGateway:
    def __init__(self) -> None:
        self.identity = VerifiedIdentity(
            app_id="cli_test",
            tenant_ref="tenant-a",
            open_id="ou_admin",
            union_id="on_admin",
            verified_name="测试管理员",
            avatar_url=None,
            profile_ref="profile_admin",
        )
        self.redeemed: list[tuple[str, str]] = []

    def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        return_to: str,
    ) -> str:
        return (
            "https://accounts.example/platform?"
            f"state={state}&code_challenge={code_challenge}&return_to={return_to}"
        )

    async def redeem(self, *, ticket: str, code_verifier: str) -> VerifiedIdentity:
        self.redeemed.append((ticket, code_verifier))
        return self.identity

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            reachable=True,
            configured=True,
            app_id="cli_test",
            tenant_ref="tenant-a",
            authorized_profiles=2,
            callback_uri="http://localhost:3000/callback",
        )


def _app(
    tmp_path: Path,
    *,
    bootstrap_token: str | None = None,
) -> tuple[object, ControlPlaneStore, FakeIdentityGateway]:
    settings = ControlPlaneSettings(
        database_path=tmp_path / "state.sqlite3",
        public_origin=ORIGIN,
    )
    store = ControlPlaneStore(settings.database_path)
    gateway = FakeIdentityGateway()
    return (
        create_app(
            settings,
            store=store,
            identity_gateway=gateway,
            bootstrap_token=bootstrap_token,
        ),
        store,
        gateway,
    )


def _state_from_redirect(response) -> str:  # type: ignore[no-untyped-def]
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def _login_callback(client: TestClient, state: str, ticket: str = "ticket-one") -> None:
    response = client.get(
        "/auth/feishu/callback",
        params={"state": state, "ticket": ticket},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _bootstrap(client: TestClient, store: ControlPlaneStore) -> None:
    token = store.issue_bootstrap(ttl_seconds=900)
    assert token is not None
    start = client.get(
        "/auth/feishu/start",
        params={"intent": "bootstrap", "bootstrap": token},
        follow_redirects=False,
    )
    _login_callback(client, _state_from_redirect(start))


def _csrf(page_text: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page_text)
    assert match is not None
    return match.group(1)


def _post(client: TestClient, path: str, data: dict[str, str]):  # type: ignore[no-untyped-def]
    return client.post(path, data=data, headers={"Origin": ORIGIN}, follow_redirects=False)


def test_bootstrap_real_identity_team_and_role_vertical_slice(tmp_path: Path) -> None:
    app, store, gateway = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        before = client.get("/start")
        assert "初始化平台管理员" in before.text

        _bootstrap(client, store)
        create_page = client.get("/start?intent=create")
        assert "测试管理员" in create_page.text
        created = _post(
            client,
            "/teams",
            {"csrf": _csrf(create_page.text), "name": "真实 QA 团队"},
        )
        assert created.status_code == 303
        assert created.headers["location"] == "/team"

        account = client.get("/account")
        team = client.get("/team")
        platform = client.get("/platform?section=overview")
        assert "真实 QA 团队" in account.text
        assert "团队管理员" in account.text
        assert "profile_admin" in account.text
        assert "测试管理员" in team.text
        assert "1 位用户 · 1 个团队 · 1 位平台管理员" in platform.text
        assert gateway.redeemed and len(gateway.redeemed[0][1]) >= 43


def test_direct_protected_page_uses_login_entry_instead_of_dead_end(tmp_path: Path) -> None:
    app, store, _ = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        before_bootstrap = client.get("/platform", follow_redirects=False)
        assert before_bootstrap.status_code == 303
        assert before_bootstrap.headers["location"] == "/start"

        _bootstrap(client, store)
        account = client.get("/account")
        logged_out = _post(client, "/logout", {"csrf": _csrf(account.text)})
        assert logged_out.status_code == 303

        after_bootstrap = client.get("/platform", follow_redirects=False)
        assert after_bootstrap.status_code == 303
        assert after_bootstrap.headers["location"] == "/auth/feishu/start?intent=account"


def test_local_first_use_page_exposes_login_without_leaking_bootstrap_token(
    tmp_path: Path,
) -> None:
    settings = ControlPlaneSettings(
        database_path=tmp_path / "state.sqlite3",
        public_origin=ORIGIN,
    )
    store = ControlPlaneStore(settings.database_path)
    token = store.issue_bootstrap(ttl_seconds=900)
    assert token is not None
    gateway = FakeIdentityGateway()
    app = create_app(
        settings,
        store=store,
        identity_gateway=gateway,
        bootstrap_token=token,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/start")
        started = client.get("/bootstrap/local", follow_redirects=False)

    assert "使用飞书账号初始化并登录" in page.text
    assert 'href="/bootstrap/local"' in page.text
    assert token not in page.text
    assert started.status_code == 303
    assert started.headers["location"].startswith("https://accounts.example/platform?")


def test_invite_auto_joins_new_identity_and_role_navigation_is_enforced(
    tmp_path: Path,
) -> None:
    app, store, gateway = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as admin_client:
        _bootstrap(admin_client, store)
        create_page = admin_client.get("/start?intent=create")
        _post(
            admin_client,
            "/teams",
            {"csrf": _csrf(create_page.text), "name": "邀请团队"},
        )
        invite_page = admin_client.get("/team?invite=1")
        issued = _post(
            admin_client,
            "/team/invites",
            {
                "csrf": _csrf(invite_page.text),
                "expires_in": "604800",
                "max_uses": "5",
            },
        )
        assert issued.status_code == 201
        invite_match = re.search(rf"{re.escape(ORIGIN)}/join/([A-Za-z0-9_-]+)", issued.text)
        assert invite_match is not None
        invite_token = invite_match.group(1)

        gateway.identity = VerifiedIdentity(
            app_id="cli_test",
            tenant_ref="tenant-a",
            open_id="ou_member",
            union_id="on_member",
            verified_name="普通成员",
            avatar_url=None,
            profile_ref="profile_member",
        )
        with TestClient(app, base_url=ORIGIN) as member_client:
            login = member_client.get(
                "/auth/feishu/start",
                params={"intent": "join", "invite": invite_token},
                follow_redirects=False,
            )
            _login_callback(member_client, _state_from_redirect(login), "ticket-member")
            account = member_client.get("/account")
            denied_team = member_client.get("/team")
            denied_platform = member_client.get("/platform")
            assert "邀请团队" in account.text
            assert "普通成员" in account.text
            assert denied_team.status_code == 403
            assert denied_platform.status_code == 403


def test_authenticated_forms_allow_missing_origin_but_reject_cross_site_and_bad_csrf(
    tmp_path: Path,
) -> None:
    app, store, _ = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        _bootstrap(client, store)
        page = client.get("/start?intent=create")
        csrf = _csrf(page.text)
        missing_origin = client.post(
            "/teams",
            data={"csrf": csrf, "name": "团队"},
            follow_redirects=False,
        )
        wrong_csrf = client.post(
            "/teams",
            data={"csrf": "wrong", "name": "团队"},
            headers={"Origin": ORIGIN},
        )
        cross_site = client.post(
            "/teams",
            data={"csrf": csrf, "name": "团队"},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        assert missing_origin.status_code == 303
        assert wrong_csrf.status_code == 403
        assert cross_site.status_code == 403


def test_last_platform_admin_cannot_remove_self(tmp_path: Path) -> None:
    app, store, _ = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        _bootstrap(client, store)
        page = client.get("/platform?section=admins")
        user_ref = store.list_platform_admins()[0].user.user_ref
        response = _post(
            client,
            f"/platform/admins/{user_ref}/revoke",
            {"csrf": _csrf(page.text)},
        )
        assert response.status_code == 409
        assert "至少保留至少" not in response.text
        assert "至少一名平台管理员" in response.text


def test_development_reset_is_explicit_and_preserves_provider_boundary(tmp_path: Path) -> None:
    app, store, _ = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        _bootstrap(client, store)
        page = client.get("/platform?section=reset")
        wrong = _post(
            client,
            "/platform/reset",
            {"csrf": _csrf(page.text), "confirmation": "reset"},
        )
        assert wrong.status_code == 400
        page = client.get("/platform?section=reset")
        reset = _post(
            client,
            "/platform/reset",
            {"csrf": _csrf(page.text), "confirmation": "RESET"},
        )
        assert reset.status_code == 200
        assert "新的 Bootstrap 链接" in reset.text
        assert "Provider 凭证" in reset.text
        assert store.has_platform_admins() is False


def test_role_navigation_only_includes_authorized_entries() -> None:
    assert visible_navigation(Viewer("member", "M")) == ("account",)
    assert visible_navigation(Viewer("team", "T", team_admin=True)) == ("account", "team")
    assert visible_navigation(Viewer("platform", "P", platform_admin=True)) == (
        "account",
        "platform",
    )


def test_security_headers_and_no_secret_rendering(tmp_path: Path) -> None:
    app, store, _ = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        _bootstrap(client, store)
        page = client.get("/platform?section=feishu")
        stylesheet = client.get("/assets/control-plane.css")
        assert page.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in page.headers["content-security-policy"]
        assert "App Secret" in page.text
        assert "refresh_token" not in page.text.lower()
        assert stylesheet.status_code == 200
        assert "--feishu-blue: #3370ff" in stylesheet.text.lower()
