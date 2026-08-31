from __future__ import annotations

import base64
import re
from pathlib import Path

from fastapi.testclient import TestClient

from feishu_auth_service.admin import (
    DELETE_PHRASE,
    AdminOutcome,
    AdminSession,
    create_admin_app,
)
from feishu_auth_service.binding import LocalBindingStore
from feishu_auth_service.feishu import TenantIdentity


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


class FakeCredentialValidator:
    def __init__(
        self,
        *,
        tenant_key: str = "tenant-a",
        tenant_name: str = "测试企业",
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.tenant_key = tenant_key
        self.tenant_name = tenant_name

    async def resolve_tenant(
        self,
        app_id: str,
        app_secret: str,
    ) -> TenantIdentity:
        self.calls.append((app_id, app_secret))
        return TenantIdentity(tenant_key=self.tenant_key, name=self.tenant_name)

    async def aclose(self) -> None:
        self.closed = True


class FakeProfileVault:
    def __init__(self) -> None:
        self.delete_calls = 0

    def delete_all(self) -> None:
        self.delete_calls += 1


def _store(tmp_path: Path) -> LocalBindingStore:
    return LocalBindingStore(tmp_path / "feishu" / "binding.json", FakeProtector())


def _admin(
    tmp_path: Path,
    profile_vault: FakeProfileVault | None = None,
    *,
    discovered_tenant_key: str = "tenant-a",
):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    validator = FakeCredentialValidator(tenant_key=discovered_tenant_key)
    session, bootstrap = AdminSession.create(expected_origin="http://testserver")
    app = create_admin_app(
        binding_store=store,
        credential_validator=validator,
        session=session,
        profile_vault=profile_vault,  # type: ignore[arg-type]
    )
    return app, store, validator, session, bootstrap


def _bootstrap(client: TestClient, token: str) -> None:
    response = client.get(f"/bootstrap/{token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def _hidden(page: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', page)
    assert match is not None, f"missing hidden field: {name}"
    return match.group(1)


def _post(client: TestClient, path: str, data: dict[str, str]):  # type: ignore[no-untyped-def]
    return client.post(path, data=data, headers={"Origin": "http://testserver"})


def test_admin_create_flow_is_preview_bound_redacted_and_read_back(tmp_path: Path) -> None:
    app, store, validator, session, bootstrap = _admin(tmp_path)

    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        _bootstrap(client, bootstrap)
        assert client.get(f"/bootstrap/{bootstrap}").status_code == 404

        form = client.get("/providers/feishu")
        assert form.status_code == 200
        assert "建立 Feishu 部署绑定" in form.text
        assert 'name="tenant_key"' not in form.text
        assert "无需填写" in form.text
        assert "wiki:node:read" in form.text
        assert "docs:document.media:download" in form.text
        assert "sheets:spreadsheet" in form.text
        assert "drive:export:readonly" in form.text

        preview = _post(
            client,
            "/providers/feishu/preview",
            {
                "csrf": session.csrf_token,
                "app_id": "cli_test",
                "tenant_key": "must-be-ignored",
                "replace_secret": "1",
                "app_secret": "secret-must-not-render",
                "app_secret_confirm": "secret-must-not-render",
            },
        )

        assert preview.status_code == 200
        assert "飞书应用凭证与企业租户已回读" in preview.text
        assert "tenant-a" in preview.text
        assert "测试企业" in preview.text
        assert "secret-must-not-render" not in preview.text
        assert validator.calls == [("cli_test", "secret-must-not-render")]

        saved = _post(
            client,
            "/providers/feishu/apply",
            {
                "csrf": session.csrf_token,
                "draft_ref": _hidden(preview.text, "draft_ref"),
                "draft_digest": _hidden(preview.text, "draft_digest"),
            },
        )

        assert saved.status_code == 200
        assert "配置已保存并回读" in saved.text
        assert "secret-must-not-render" not in saved.text
        assert store.load().app_secret == "secret-must-not-render"
        assert store.load().allowed_tenant_key == "tenant-a"
        assert "secret-must-not-render" not in store.path.read_text(encoding="utf-8")
        assert session.outcome is AdminOutcome.SAVED

    assert validator.closed is True


def test_admin_edit_keeps_secret_unless_replacement_is_explicit(tmp_path: Path) -> None:
    app, store, validator, session, bootstrap = _admin(tmp_path)
    store.save(app_id="cli_old", app_secret="existing-secret", allowed_tenant_key="tenant-a")

    with TestClient(app) as client:
        _bootstrap(client, bootstrap)
        ordinary = client.get("/providers/feishu")
        replacement = client.get("/providers/feishu?replace_secret=1")

        assert 'name="app_secret"' not in ordinary.text
        assert 'name="app_secret"' in replacement.text

        preview = _post(
            client,
            "/providers/feishu/preview",
            {
                "csrf": session.csrf_token,
                "app_id": "cli_old",
            },
        )

        assert preview.status_code == 200
        assert "保留" in preview.text
        assert validator.calls == [("cli_old", "existing-secret")]


def test_admin_delete_requires_phrase_then_verifies_absence(tmp_path: Path) -> None:
    profiles = FakeProfileVault()
    app, store, _, session, bootstrap = _admin(tmp_path, profiles)
    store.save(app_id="cli_test", app_secret="existing-secret", allowed_tenant_key="tenant-a")

    with TestClient(app) as client:
        _bootstrap(client, bootstrap)
        preview = _post(
            client,
            "/providers/feishu/delete/preview",
            {"csrf": session.csrf_token},
        )
        fields = {
            "csrf": session.csrf_token,
            "draft_ref": _hidden(preview.text, "draft_ref"),
            "draft_digest": _hidden(preview.text, "draft_digest"),
        }

        rejected = _post(
            client,
            "/providers/feishu/delete",
            {**fields, "confirmation": "delete"},
        )
        assert rejected.status_code == 400
        assert store.exists() is True

        deleted = _post(
            client,
            "/providers/feishu/delete",
            {**fields, "confirmation": DELETE_PHRASE},
        )

        assert deleted.status_code == 200
        assert "本机部署绑定已删除" in deleted.text
        assert "飞书远端" in deleted.text
        assert store.exists() is False
        assert profiles.delete_calls == 1
        assert session.outcome is AdminOutcome.DELETED


def test_admin_identity_boundary_change_invalidates_all_profiles(tmp_path: Path) -> None:
    profiles = FakeProfileVault()
    app, store, _, session, bootstrap = _admin(
        tmp_path,
        profiles,
        discovered_tenant_key="tenant-b",
    )
    store.save(
        app_id="cli_test",
        app_secret="existing-secret",
        allowed_tenant_key="tenant-a",
    )

    with TestClient(app) as client:
        _bootstrap(client, bootstrap)
        preview = _post(
            client,
            "/providers/feishu/preview",
            {
                "csrf": session.csrf_token,
                "app_id": "cli_test",
            },
        )
        saved = _post(
            client,
            "/providers/feishu/apply",
            {
                "csrf": session.csrf_token,
                "draft_ref": _hidden(preview.text, "draft_ref"),
                "draft_digest": _hidden(preview.text, "draft_digest"),
            },
        )

        assert saved.status_code == 200
        assert store.load().allowed_tenant_key == "tenant-b"
        assert profiles.delete_calls == 1


def test_admin_write_requires_exact_origin_and_csrf(tmp_path: Path) -> None:
    app, _, _, session, bootstrap = _admin(tmp_path)

    with TestClient(app) as client:
        _bootstrap(client, bootstrap)
        wrong_origin = client.post(
            "/providers/feishu/preview",
            data={"csrf": session.csrf_token},
            headers={"Origin": "http://evil.example"},
        )
        wrong_csrf = client.post(
            "/providers/feishu/preview",
            data={"csrf": "wrong"},
            headers={"Origin": "http://testserver"},
        )
        opaque_cross_site = client.post(
            "/providers/feishu/preview",
            data={"csrf": session.csrf_token},
            headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
        )
        opaque_without_metadata = client.post(
            "/providers/feishu/preview",
            data={"csrf": session.csrf_token},
            headers={"Origin": "null"},
        )

        assert wrong_origin.status_code == 403
        assert wrong_csrf.status_code == 403
        assert opaque_cross_site.status_code == 403
        assert opaque_without_metadata.status_code == 403


def test_admin_write_accepts_edge_null_origin_with_same_origin_fetch_metadata(
    tmp_path: Path,
) -> None:
    app, _, validator, session, bootstrap = _admin(tmp_path)

    with TestClient(app) as client:
        _bootstrap(client, bootstrap)
        preview = client.post(
            "/providers/feishu/preview",
            data={
                "csrf": session.csrf_token,
                "app_id": "cli_test",
                "replace_secret": "1",
                "app_secret": "secret-must-not-render",
                "app_secret_confirm": "secret-must-not-render",
            },
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        )

        assert preview.status_code == 200
        assert "飞书应用凭证与企业租户已回读" in preview.text
        assert validator.calls == [("cli_test", "secret-must-not-render")]


def test_admin_session_expires_and_cannot_be_reused() -> None:
    now = [100.0]
    session, bootstrap = AdminSession.create(
        expected_origin="http://testserver",
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    cookie = session.consume_bootstrap(bootstrap)
    assert session.authorizes(cookie) is True

    now[0] = 111.0

    assert session.authorizes(cookie) is False
    assert session.outcome is AdminOutcome.EXPIRED
