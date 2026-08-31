from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from feishu_auth_service.admin_styles import CONTROL_ROOM_STYLES as _CONTROL_ROOM_STYLES
from feishu_auth_service.binding import (
    BindingError,
    BindingSummary,
    LocalBindingStore,
    validate_application_credentials,
    validate_binding_values,
)
from feishu_auth_service.config import DEFAULT_OAUTH_SCOPES
from feishu_auth_service.feishu import AppCredentialValidator, FeishuApiError
from feishu_auth_service.profiles import ProfileError, ProfileVault


SESSION_COOKIE = "workspace_capability_admin"
DELETE_PHRASE = "删除本机部署绑定"
LOGGER = logging.getLogger(__name__)


class AdminOutcome(StrEnum):
    PENDING = "pending"
    SAVED = "saved"
    DELETED = "deleted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DraftAction(StrEnum):
    SAVE = "save"
    DELETE = "delete"


class AdminSession:
    def __init__(
        self,
        *,
        expected_origin: str,
        bootstrap_token: str,
        ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.expected_origin = expected_origin.rstrip("/")
        self._bootstrap_digest = _digest(bootstrap_token)
        self._bootstrap_consumed = False
        self._cookie_digest: str | None = None
        self._csrf_token = secrets.token_urlsafe(32)
        self._signing_key = secrets.token_bytes(32)
        self._clock = clock
        self._expires_at = clock() + ttl_seconds
        self._outcome = AdminOutcome.PENDING
        self._lock = threading.Lock()

    @classmethod
    def create(
        cls,
        *,
        expected_origin: str,
        ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> tuple[AdminSession, str]:
        bootstrap_token = secrets.token_urlsafe(32)
        return (
            cls(
                expected_origin=expected_origin,
                bootstrap_token=bootstrap_token,
                ttl_seconds=ttl_seconds,
                clock=clock,
            ),
            bootstrap_token,
        )

    @property
    def outcome(self) -> AdminOutcome:
        with self._lock:
            self._expire_if_needed()
            return self._outcome

    @property
    def csrf_token(self) -> str:
        return self._csrf_token

    @property
    def expires_at(self) -> float:
        return self._expires_at

    def consume_bootstrap(self, token: str) -> str | None:
        with self._lock:
            self._expire_if_needed()
            if self._outcome is not AdminOutcome.PENDING or self._bootstrap_consumed:
                return None
            if not hmac.compare_digest(_digest(token), self._bootstrap_digest):
                return None
            self._bootstrap_consumed = True
            cookie_token = secrets.token_urlsafe(32)
            self._cookie_digest = _digest(cookie_token)
            return cookie_token

    def authorizes(self, cookie_token: str | None) -> bool:
        if not cookie_token:
            return False
        with self._lock:
            self._expire_if_needed()
            return (
                self._outcome is AdminOutcome.PENDING
                and self._cookie_digest is not None
                and hmac.compare_digest(_digest(cookie_token), self._cookie_digest)
            )

    def trusts_write_origin(
        self,
        *,
        origin: str | None,
        sec_fetch_site: str | None,
    ) -> bool:
        return origin == self.expected_origin or (
            origin == "null" and sec_fetch_site == "same-origin"
        )

    def authorizes_write(
        self,
        *,
        cookie_token: str | None,
        csrf: str,
        origin: str | None,
        sec_fetch_site: str | None = None,
    ) -> bool:
        return (
            self.authorizes(cookie_token)
            and hmac.compare_digest(csrf, self._csrf_token)
            and self.trusts_write_origin(
                origin=origin,
                sec_fetch_site=sec_fetch_site,
            )
        )

    def sign(self, payload: str) -> str:
        return hmac.new(self._signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def finish(self, outcome: AdminOutcome) -> bool:
        if outcome is AdminOutcome.PENDING:
            raise ValueError("A terminal admin outcome is required")
        with self._lock:
            self._expire_if_needed()
            if self._outcome is not AdminOutcome.PENDING:
                return False
            self._outcome = outcome
            self._cookie_digest = None
            return True

    def _expire_if_needed(self) -> None:
        if self._outcome is AdminOutcome.PENDING and self._clock() >= self._expires_at:
            self._outcome = AdminOutcome.EXPIRED
            self._cookie_digest = None


@dataclass(frozen=True, slots=True)
class AdminDraft:
    ref: str
    digest: str
    action: DraftAction
    app_id: str | None = None
    app_secret: str | None = None
    tenant_key: str | None = None
    tenant_name: str | None = None
    secret_action: str | None = None


class AdminDraftStore:
    def __init__(self, session: AdminSession) -> None:
        self._session = session
        self._draft: AdminDraft | None = None
        self._lock = threading.Lock()

    def create_save(
        self,
        *,
        app_id: str,
        app_secret: str,
        tenant_key: str,
        tenant_name: str | None,
        secret_action: str,
    ) -> AdminDraft:
        ref = f"draft_{secrets.token_urlsafe(18)}"
        canonical = json.dumps(
            {
                "action": DraftAction.SAVE,
                "ref": ref,
                "app_id": app_id,
                "tenant_key": tenant_key,
                "tenant_name": tenant_name,
                "secret_digest": hashlib.sha256(app_secret.encode()).hexdigest(),
                "secret_action": secret_action,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        draft = AdminDraft(
            ref=ref,
            digest=self._session.sign(canonical),
            action=DraftAction.SAVE,
            app_id=app_id,
            app_secret=app_secret,
            tenant_key=tenant_key,
            tenant_name=tenant_name,
            secret_action=secret_action,
        )
        with self._lock:
            self._draft = draft
        return draft

    def create_delete(self) -> AdminDraft:
        ref = f"draft_{secrets.token_urlsafe(18)}"
        canonical = json.dumps(
            {"action": DraftAction.DELETE, "ref": ref},
            sort_keys=True,
            separators=(",", ":"),
        )
        draft = AdminDraft(
            ref=ref,
            digest=self._session.sign(canonical),
            action=DraftAction.DELETE,
        )
        with self._lock:
            self._draft = draft
        return draft

    def consume(self, *, ref: str, digest: str, action: DraftAction) -> AdminDraft | None:
        with self._lock:
            draft = self._draft
            if draft is None:
                return None
            if (
                draft.action is not action
                or not hmac.compare_digest(draft.ref, ref)
                or not hmac.compare_digest(draft.digest, digest)
            ):
                return None
            self._draft = None
            return draft

    def clear(self) -> None:
        with self._lock:
            self._draft = None


def create_admin_app(
    *,
    binding_store: LocalBindingStore,
    credential_validator: AppCredentialValidator,
    session: AdminSession,
    profile_vault: ProfileVault | None = None,
    on_terminal: Callable[[], None] | None = None,
) -> FastAPI:
    drafts = AdminDraftStore(session)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        drafts.clear()
        await credential_validator.aclose()

    app = FastAPI(
        title="Workspace Capability Admin",
        version="0.7.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.admin_session = session
    app.state.binding_store = binding_store
    app.state.profile_vault = profile_vault

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/bootstrap/{token}")
    async def bootstrap(token: str) -> Response:
        cookie_token = session.consume_bootstrap(token)
        if cookie_token is None:
            return _not_found_page()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            cookie_token,
            httponly=True,
            secure=False,
            samesite="strict",
            max_age=600,
            path="/",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def portal(request: Request) -> HTMLResponse:
        if not _authorized(request, session):
            return _not_found_page()
        return HTMLResponse(_portal_page(binding_store.summary(), session.csrf_token))

    @app.get("/providers/feishu", response_class=HTMLResponse)
    async def feishu_form(request: Request, replace_secret: int = 0) -> HTMLResponse:
        if not _authorized(request, session):
            return _not_found_page()
        summary = binding_store.summary()
        return HTMLResponse(
            _form_page(
                summary=summary,
                csrf=session.csrf_token,
                replace_secret=bool(replace_secret),
            )
        )

    @app.post("/providers/feishu/preview", response_class=HTMLResponse)
    async def preview_save(request: Request) -> HTMLResponse:
        form = await request.form()
        if not _authorized_write(request, session, _form_text(form, "csrf")):
            return _forbidden_page()
        summary = binding_store.summary()
        replace_secret = _form_text(form, "replace_secret") == "1" or summary is None
        app_id = _form_text(form, "app_id")
        try:
            if replace_secret:
                first_secret = _form_text(form, "app_secret")
                second_secret = _form_text(form, "app_secret_confirm")
                if not hmac.compare_digest(first_secret, second_secret):
                    raise BindingError("两次输入的 App Secret 不一致")
                app_secret = first_secret
                secret_action = "新增" if summary is None else "替换"
            else:
                app_secret = binding_store.load().app_secret
                secret_action = "保留"
            app_id, app_secret = validate_application_credentials(
                app_id,
                app_secret,
            )
            tenant = await credential_validator.resolve_tenant(app_id, app_secret)
            app_id, app_secret, tenant_key = validate_binding_values(
                app_id,
                app_secret,
                tenant.tenant_key,
            )
        except BindingError as exc:
            return HTMLResponse(
                _form_page(
                    summary=summary,
                    csrf=session.csrf_token,
                    replace_secret=replace_secret,
                    app_id=app_id,
                    error=str(exc),
                ),
                status_code=400,
            )
        except FeishuApiError:
            return HTMLResponse(
                _form_page(
                    summary=summary,
                    csrf=session.csrf_token,
                    replace_secret=replace_secret,
                    app_id=app_id,
                    error="飞书未接受应用凭证，或无法自动回读所属企业租户，请检查应用后重试。",
                ),
                status_code=400,
            )
        draft = drafts.create_save(
            app_id=app_id,
            app_secret=app_secret,
            tenant_key=tenant_key,
            tenant_name=tenant.name,
            secret_action=secret_action,
        )
        return HTMLResponse(
            _save_preview_page(
                before=summary,
                draft=draft,
                csrf=session.csrf_token,
                path=binding_store.path,
            )
        )

    @app.post("/providers/feishu/apply", response_class=HTMLResponse)
    async def apply_save(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        form = await request.form()
        if not _authorized_write(request, session, _form_text(form, "csrf")):
            return _forbidden_page()
        draft = drafts.consume(
            ref=_form_text(form, "draft_ref"),
            digest=_form_text(form, "draft_digest"),
            action=DraftAction.SAVE,
        )
        if (
            draft is None
            or draft.app_id is None
            or draft.app_secret is None
            or draft.tenant_key is None
        ):
            return _stale_draft_page()
        before = binding_store.summary()
        try:
            if profile_vault is not None and before is not None and (
                before.app_id != draft.app_id
                or before.allowed_tenant_key != draft.tenant_key
            ):
                profile_vault.delete_all()
            binding_store.save(
                app_id=draft.app_id,
                app_secret=draft.app_secret,
                allowed_tenant_key=draft.tenant_key,
            )
            summary = binding_store.summary()
        except (BindingError, ProfileError):
            return HTMLResponse(
                _message_page(
                    "配置未写入",
                    "保存或回读失败，部署绑定不会报告成功；若本次涉及 App ID 或租户变更，旧 Profile 可能已为安全起见失效，需要重新授权。",
                    state="error",
                ),
                status_code=500,
            )
        if summary is None or not summary.secret_configured:
            return HTMLResponse(
                _message_page("配置未写入", "保存后的配置回读不完整。", state="error"),
                status_code=500,
            )
        session.finish(AdminOutcome.SAVED)
        response = HTMLResponse(_saved_page(summary))
        response.delete_cookie(SESSION_COOKIE, path="/")
        _schedule_terminal(background_tasks, on_terminal)
        return response

    @app.post("/providers/feishu/delete/preview", response_class=HTMLResponse)
    async def preview_delete(request: Request) -> HTMLResponse:
        form = await request.form()
        if not _authorized_write(request, session, _form_text(form, "csrf")):
            return _forbidden_page()
        summary = binding_store.summary()
        if summary is None:
            return HTMLResponse(
                _message_page("无需删除", "当前没有本机 Feishu 部署绑定。", state="idle"),
                status_code=409,
            )
        draft = drafts.create_delete()
        return HTMLResponse(_delete_preview_page(summary, draft, session.csrf_token))

    @app.post("/providers/feishu/delete", response_class=HTMLResponse)
    async def apply_delete(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        form = await request.form()
        if not _authorized_write(request, session, _form_text(form, "csrf")):
            return _forbidden_page()
        if not hmac.compare_digest(
            _form_text(form, "confirmation").encode("utf-8"),
            DELETE_PHRASE.encode("utf-8"),
        ):
            return HTMLResponse(
                _message_page(
                    "确认短语不匹配",
                    f"请输入完整短语：{DELETE_PHRASE}",
                    state="error",
                ),
                status_code=400,
            )
        draft = drafts.consume(
            ref=_form_text(form, "draft_ref"),
            digest=_form_text(form, "draft_digest"),
            action=DraftAction.DELETE,
        )
        if draft is None:
            return _stale_draft_page()
        try:
            if profile_vault is not None:
                profile_vault.delete_all()
            binding_store.delete()
        except (BindingError, ProfileError):
            return HTMLResponse(
                _message_page("部署解绑失败", "本机绑定仍然存在，未报告删除成功。", state="error"),
                status_code=500,
            )
        session.finish(AdminOutcome.DELETED)
        response = HTMLResponse(_deleted_page(binding_store.path))
        response.delete_cookie(SESSION_COOKIE, path="/")
        _schedule_terminal(background_tasks, on_terminal)
        return response

    @app.post("/cancel", response_class=HTMLResponse)
    async def cancel(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        form = await request.form()
        if not _authorized_write(request, session, _form_text(form, "csrf")):
            return _forbidden_page()
        session.finish(AdminOutcome.CANCELLED)
        response = HTMLResponse(
            _message_page("配置会话已取消", "没有写入或删除本机部署绑定。", state="idle")
        )
        response.delete_cookie(SESSION_COOKIE, path="/")
        _schedule_terminal(background_tasks, on_terminal)
        return response

    return app


def _authorized(request: Request, session: AdminSession) -> bool:
    return session.authorizes(request.cookies.get(SESSION_COOKIE))


def _authorized_write(request: Request, session: AdminSession, csrf: str) -> bool:
    cookie_token = request.cookies.get(SESSION_COOKIE)
    origin = request.headers.get("origin")
    sec_fetch_site = request.headers.get("sec-fetch-site")
    session_ok = session.authorizes(cookie_token)
    csrf_ok = hmac.compare_digest(csrf, session.csrf_token)
    origin_ok = session.trusts_write_origin(
        origin=origin,
        sec_fetch_site=sec_fetch_site,
    )
    allowed = session_ok and csrf_ok and origin_ok
    if not allowed:
        LOGGER.warning(
            "admin_write_denied session_ok=%s csrf_ok=%s origin_ok=%s "
            "origin=%r expected_origin=%r sec_fetch_site=%r referer_present=%s",
            session_ok,
            csrf_ok,
            origin_ok,
            origin,
            session.expected_origin,
            sec_fetch_site,
            bool(request.headers.get("referer")),
        )
    return allowed


def _form_text(form: object, name: str) -> str:
    getter = getattr(form, "get", None)
    if getter is None:
        return ""
    value = getter(name, "")
    return value if isinstance(value, str) else ""


def _schedule_terminal(
    background_tasks: BackgroundTasks,
    on_terminal: Callable[[], None] | None,
) -> None:
    if on_terminal is not None:
        background_tasks.add_task(on_terminal)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _portal_page(summary: BindingSummary | None, csrf: str) -> str:
    configured = summary is not None
    status = "已配置" if configured else "未配置"
    status_class = "ready" if configured else "idle"
    details = (
        f"<dl class='facts provider-facts'><div><dt>App ID</dt><dd><code>{_e(summary.app_id)}</code></dd></div>"
        f"<div><dt>准入租户</dt><dd><code>{_e(summary.allowed_tenant_key)}</code></dd></div>"
        "<div><dt>App Secret</dt><dd>已由 DPAPI 保护</dd></div></dl>"
        if summary is not None
        else "<div class='provider-empty'><strong>尚未建立部署绑定</strong><p>配置飞书应用后即可启用文档读取与受管 Sheet 写入。</p></div>"
    )
    action = "检查或修改" if configured else "开始配置"
    body = f"""
    <section class="hero compact portal-hero">
      <div>
        <p class="eyebrow">Local capability console</p>
        <h1>管理本机公共能力。</h1>
        <p class="lede">集中查看连接状态，并按 Provider 独立管理配置与凭据。</p>
      </div>
      <aside class="session-note" aria-label="本机配置会话说明">
        <span class="session-note-label"><i></i>Local session</span>
        <strong>凭据只在服务端处理</strong>
        <small>页面不会回传已保存的 App Secret</small>
      </aside>
    </section>
    <section class="provider-card">
      <div class="provider-head">
        <div class="provider-identity">
          <span class="provider-symbol" aria-hidden="true">FS</span>
          <div><p class="eyebrow">Provider / Feishu</p><h2>飞书读写基础能力</h2><p class="provider-caption">文档读取 · 受管 Sheet 写入 · OAuth</p></div>
        </div>
        <span class="status {status_class}"><i></i>{status}</span>
      </div>
      {details}
      <div class="provider-actions">
        {_cancel_form(csrf)}
        <a class="button primary" href="/providers/feishu">{action}<span aria-hidden="true">→</span></a>
      </div>
    </section>
    """
    return _shell("公共能力管理门户", body, active="portal", configured=configured)


def _form_page(
    *,
    summary: BindingSummary | None,
    csrf: str,
    replace_secret: bool,
    app_id: str | None = None,
    error: str | None = None,
) -> str:
    configured = summary is not None
    app_id_value = app_id if app_id is not None else (summary.app_id if summary else "")
    secret_fields = ""
    if not configured or replace_secret:
        secret_fields = """
        <div class="field span-2">
          <label for="app_secret">App Secret</label>
          <input id="app_secret" name="app_secret" type="password" required maxlength="512" autocomplete="new-password">
          <p class="hint">只在本次配置会话的服务端内存中处理。</p>
        </div>
        <div class="field span-2">
          <label for="app_secret_confirm">再次输入 App Secret</label>
          <input id="app_secret_confirm" name="app_secret_confirm" type="password" required maxlength="512" autocomplete="new-password">
        </div>
        <input type="hidden" name="replace_secret" value="1">
        """
    else:
        secret_fields = """
        <div class="secret-state span-2">
          <div><strong>App Secret 已配置</strong><span>现有值不会发送到浏览器。</span></div>
          <a class="text-link" href="/providers/feishu?replace_secret=1">替换 App Secret</a>
        </div>
        """
    error_block = f"<div class='notice error' role='alert'>{_e(error)}</div>" if error else ""
    title = "修改 Feishu 部署绑定" if configured else "建立 Feishu 部署绑定"
    body = f"""
    <section class="hero compact">
      <p class="eyebrow">Provider / Feishu / Configure</p>
      <h1>{title}</h1>
      <p class="lede">保存前先验证飞书应用凭证，再生成一份不含 Secret 的差异预览。</p>
    </section>
    {error_block}
    <form class="panel form-grid" method="post" action="/providers/feishu/preview" autocomplete="off">
      <input type="hidden" name="csrf" value="{_e(csrf)}">
      <div class="field span-2">
        <label for="app_id">App ID</label>
        <input id="app_id" name="app_id" value="{_e(app_id_value)}" required maxlength="512" spellcheck="false" autocomplete="off">
      </div>
      <div class="readonly span-2">
        <p class="eyebrow">准入企业租户</p>
        <p><strong>{_e(summary.allowed_tenant_key) if summary else "无需填写"}</strong></p>
        <p class="hint">提交后使用飞书应用凭证自动回读企业名称与 tenant_key，并在预览中由你确认；真实用户 OAuth 回调仍会再次核对。</p>
      </div>
      {secret_fields}
      <div class="readonly span-2">
        <p class="eyebrow">固定运行合同</p>
        <dl class="facts compact-facts">
          <div><dt>回调</dt><dd><code>http://localhost:3000/callback</code></dd></div>
          <div><dt>Scope</dt><dd><code>{_e(' '.join(DEFAULT_OAUTH_SCOPES))}</code></dd></div>
          <div><dt>监听</dt><dd><code>127.0.0.1:3000</code></dd></div>
        </dl>
      </div>
      <div class="actions span-2">
        <a class="button quiet" href="/">返回门户</a>
        <button class="button primary" type="submit">验证并预览</button>
      </div>
    </form>
    {_delete_entry(summary, csrf)}
    """
    return _shell(title, body, active="configure", configured=configured)


def _save_preview_page(
    *,
    before: BindingSummary | None,
    draft: AdminDraft,
    csrf: str,
    path: object,
) -> str:
    old_app = before.app_id if before else "未配置"
    old_tenant = before.allowed_tenant_key if before else "未配置"
    tenant_name = draft.tenant_name or "飞书未返回企业名称"
    body = f"""
    <section class="hero compact">
      <p class="eyebrow">Provider / Feishu / Preview</p>
      <h1>确认这一次本机写入。</h1>
      <p class="lede">预览已绑定当前草案；返回修改后必须重新验证。</p>
    </section>
    <section class="panel">
      <div class="validation-stamp"><span>✓</span><div><strong>飞书应用凭证与企业租户已回读</strong><p>临时 tenant_access_token 已丢弃，没有写入磁盘。</p></div></div>
      <table class="diff-table">
        <thead><tr><th>字段</th><th>当前</th><th>保存后</th></tr></thead>
        <tbody>
          <tr><th>App ID</th><td><code>{_e(old_app)}</code></td><td><code>{_e(draft.app_id)}</code></td></tr>
          <tr><th>准入租户</th><td><code>{_e(old_tenant)}</code></td><td><code>{_e(draft.tenant_key)}</code></td></tr>
          <tr><th>企业名称</th><td>—</td><td>{_e(tenant_name)}</td></tr>
          <tr><th>App Secret</th><td colspan="2"><span class="pill">{_e(draft.secret_action)}</span> 内容始终隐藏</td></tr>
          <tr><th>本机路径</th><td colspan="2"><code>{_e(path)}</code></td></tr>
        </tbody>
      </table>
      <form method="post" action="/providers/feishu/apply">
        <input type="hidden" name="csrf" value="{_e(csrf)}">
        <input type="hidden" name="draft_ref" value="{_e(draft.ref)}">
        <input type="hidden" name="draft_digest" value="{_e(draft.digest)}">
        <div class="actions"><a class="button quiet" href="/providers/feishu">返回修改</a><button class="button primary" type="submit">确认保存</button></div>
      </form>
    </section>
    """
    return _shell("确认 Feishu 配置", body, active="verify", configured=before is not None)


def _delete_entry(summary: BindingSummary | None, csrf: str) -> str:
    if summary is None:
        return ""
    return f"""
    <section class="danger-zone">
      <div><p class="eyebrow">Danger zone</p><h2>删除本机部署绑定</h2><p>只删除本机配置，不会修改飞书远端应用或用户授权。</p></div>
      <form method="post" action="/providers/feishu/delete/preview"><input type="hidden" name="csrf" value="{_e(csrf)}"><button class="button danger" type="submit">查看删除范围</button></form>
    </section>
    """


def _delete_preview_page(summary: BindingSummary, draft: AdminDraft, csrf: str) -> str:
    body = f"""
    <section class="hero compact danger-hero">
      <p class="eyebrow">Provider / Feishu / Delete</p>
      <h1>删除后，本机将无法发起 OAuth。</h1>
      <p class="lede">本机删除不等于飞书远端撤销。需要恢复时必须重新建立完整部署绑定。</p>
    </section>
    <section class="panel danger-panel">
      <dl class="facts">
        <div><dt>配置路径</dt><dd><code>{_e(summary.path)}</code></dd></div>
        <div><dt>App ID</dt><dd><code>{_e(summary.app_id)}</code></dd></div>
        <div><dt>准入租户</dt><dd><code>{_e(summary.allowed_tenant_key)}</code></dd></div>
        <div><dt>App Secret</dt><dd>DPAPI 密文将被删除</dd></div>
      </dl>
      <form class="delete-confirm" method="post" action="/providers/feishu/delete" autocomplete="off">
        <input type="hidden" name="csrf" value="{_e(csrf)}">
        <input type="hidden" name="draft_ref" value="{_e(draft.ref)}">
        <input type="hidden" name="draft_digest" value="{_e(draft.digest)}">
        <label for="confirmation">输入确认短语 <code>{DELETE_PHRASE}</code></label>
        <input id="confirmation" name="confirmation" required autocomplete="off">
        <div class="actions"><a class="button quiet" href="/providers/feishu">取消</a><button class="button danger" type="submit">确认删除本机绑定</button></div>
      </form>
    </section>
    """
    return _shell("删除 Feishu 本机绑定", body, active="configure", configured=True)


def _saved_page(summary: BindingSummary) -> str:
    body = f"""
    <section class="result-block success"><span class="result-mark">✓</span><p class="eyebrow">Readback complete</p><h1>配置已保存并回读。</h1><p>绑定文件与 DPAPI 密文均已验证。配置服务即将关闭，启动器会继续启动正常 OAuth 服务。</p>
    <dl class="facts"><div><dt>App ID</dt><dd><code>{_e(summary.app_id)}</code></dd></div><div><dt>准入租户</dt><dd><code>{_e(summary.allowed_tenant_key)}</code></dd></div><div><dt>状态</dt><dd>已配置</dd></div></dl></section>
    """
    return _shell("Feishu 配置已保存", body, active="ready", configured=True)


def _deleted_page(path: object) -> str:
    body = f"""
    <section class="result-block deleted"><span class="result-mark">—</span><p class="eyebrow">Readback complete</p><h1>本机部署绑定已删除。</h1><p>已确认绑定文件不存在；飞书远端应用、密钥和用户授权没有被修改。</p><dl class="facts"><div><dt>原路径</dt><dd><code>{_e(path)}</code></dd></div><div><dt>本机状态</dt><dd>未配置</dd></div></dl></section>
    """
    return _shell("Feishu 本机绑定已删除", body, active="configure", configured=False)


def _cancel_form(csrf: str) -> str:
    return f"""
    <form class="session-exit" method="post" action="/cancel"><input type="hidden" name="csrf" value="{_e(csrf)}"><button class="text-button" type="submit">取消并关闭配置会话</button></form>
    """


def _message_page(title: str, message: str, *, state: str) -> str:
    body = f"<section class='result-block {state}'><p class='eyebrow'>Configuration session</p><h1>{_e(title)}</h1><p>{_e(message)}</p></section>"
    return _shell(title, body, active="configure", configured=False)


def _not_found_page() -> HTMLResponse:
    return HTMLResponse(
        _message_page("页面不可用", "配置会话不存在、已使用或已经结束。", state="idle"),
        status_code=404,
    )


def _forbidden_page() -> HTMLResponse:
    return HTMLResponse(
        _message_page("请求未获准", "配置会话、来源或确认值无效。", state="error"),
        status_code=403,
    )


def _stale_draft_page() -> HTMLResponse:
    return HTMLResponse(
        _message_page("预览已失效", "配置草案发生变化或已经使用，请重新生成预览。", state="error"),
        status_code=409,
    )


def _shell(title: str, body: str, *, active: str, configured: bool) -> str:
    states = [
        ("portal", "门户", "统一入口"),
        ("configure", "Feishu", "独立配置"),
        ("verify", "验证", "凭证与预览"),
        ("ready", "就绪", "OAuth 服务"),
    ]
    state_order = [item[0] for item in states]
    try:
        active_index = state_order.index(active)
    except ValueError:
        active_index = 0
    rail_items = []
    for index, (key, label, detail) in enumerate(states):
        css = "active" if index == active_index else ("complete" if index < active_index else "")
        current = " aria-current='step'" if index == active_index else ""
        rail_items.append(
            f"<li class='{css}'{current}><span class='rail-node'></span><div><strong>{label}</strong><small>{detail}</small></div></li>"
        )
    binding_badge = "binding / ready" if configured else "binding / absent"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
  <style>{_STYLES}</style>
  <style>{_CONTROL_ROOM_STYLES}</style>
</head>
<body>
  <div class="app-frame">
    <aside class="rail">
      <a class="wordmark" href="/" aria-label="Workspace Capabilities 门户"><span>WC</span><div>Workspace<br>Capabilities</div></a>
      <nav class="rail-nav" aria-label="配置流程"><ol>{''.join(rail_items)}</ol></nav>
      <div class="binding-badge"><span></span>{binding_badge}</div>
    </aside>
    <main><div class="page">{body}<footer><span>本机管理员会话</span><span>Secret 仅在服务端处理</span></footer></div></main>
  </div>
</body>
</html>"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


_STYLES = """
:root{--ink:#0b1220;--muted:#5d6b82;--canvas:#f4f7fb;--surface:#fff;--line:#dce3ee;--blue:#175cd3;--blue-soft:#eaf1ff;--green:#168a6a;--green-soft:#e7f6f1;--red:#c4320a;--red-soft:#fff0eb;--shadow:0 18px 50px rgba(22,34,56,.08);font-family:"Segoe UI Variable Text","Microsoft YaHei UI",sans-serif;color:var(--ink);background:var(--canvas)}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:linear-gradient(135deg,#f8fafc 0%,var(--canvas) 52%,#edf3fb 100%)}button,input{font:inherit}.app-frame{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}.rail{position:sticky;top:0;height:100vh;padding:30px 28px;background:var(--ink);color:#fff;display:flex;flex-direction:column}.wordmark{display:flex;gap:12px;align-items:center;color:#fff;text-decoration:none;font-family:Bahnschrift,"Segoe UI Variable Display",sans-serif;font-size:13px;line-height:1.15;letter-spacing:.04em}.wordmark>span{display:grid;place-items:center;width:44px;height:44px;border:1px solid rgba(255,255,255,.34);font-size:17px;letter-spacing:.08em}.rail ol{list-style:none;padding:0;margin:72px 0 0;position:relative}.rail ol:before{content:"";position:absolute;left:8px;top:11px;bottom:13px;width:1px;background:rgba(255,255,255,.18)}.rail li{position:relative;display:flex;gap:18px;align-items:flex-start;margin:0 0 34px;color:#7f8ba0}.rail-node{position:relative;z-index:1;width:17px;height:17px;border-radius:50%;border:1px solid #657086;background:var(--ink);box-shadow:0 0 0 6px var(--ink)}.rail li.complete{color:#a9b8d0}.rail li.complete .rail-node{border-color:#80a9ff;background:#80a9ff}.rail li.active{color:#fff}.rail li.active .rail-node{border:4px solid #fff;background:var(--blue);box-shadow:0 0 0 6px var(--ink),0 0 0 8px rgba(92,143,255,.42)}.rail li strong{display:block;font:600 14px/1.2 Bahnschrift,"Segoe UI Variable Display",sans-serif;letter-spacing:.04em}.rail li small{display:block;margin-top:5px;font-size:12px;color:inherit}.binding-badge{margin-top:auto;display:flex;gap:8px;align-items:center;padding-top:18px;border-top:1px solid rgba(255,255,255,.14);font:12px/1 Consolas,"Cascadia Mono",monospace;color:#b8c4d8}.binding-badge span{width:7px;height:7px;border-radius:50%;background:#46d5aa}main{width:min(960px,100%);padding:58px 64px 28px}.hero{margin:0 0 32px}.hero.compact{max-width:780px}.eyebrow{margin:0 0 11px;color:var(--blue);font:700 11px/1.2 Consolas,"Cascadia Mono",monospace;letter-spacing:.16em;text-transform:uppercase}.hero h1,.result-block h1{margin:0;max-width:780px;font:650 clamp(34px,5vw,60px)/1.02 Bahnschrift,"Segoe UI Variable Display","Microsoft YaHei UI",sans-serif;letter-spacing:-.035em}.hero.compact h1{font-size:clamp(32px,4.2vw,50px)}.lede{max-width:720px;margin:20px 0 0;color:var(--muted);font-size:17px;line-height:1.72}.provider-card,.panel,.danger-zone,.result-block{background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow)}.provider-card,.panel{padding:30px}.provider-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding-bottom:24px;border-bottom:1px solid var(--line)}h2{margin:0;font:650 23px/1.2 Bahnschrift,"Segoe UI Variable Display","Microsoft YaHei UI",sans-serif}.status{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);padding:7px 10px;font-size:12px;font-weight:700}.status i{width:7px;height:7px;border-radius:50%;background:var(--muted)}.status.ready{color:var(--green);background:var(--green-soft);border-color:#bce7d9}.status.ready i{background:var(--green)}.status.idle{color:var(--muted);background:#f7f9fc}.facts{margin:24px 0 0}.facts>div{display:grid;grid-template-columns:150px minmax(0,1fr);gap:18px;padding:13px 0;border-bottom:1px solid var(--line)}.facts dt{color:var(--muted);font-size:13px}.facts dd{margin:0;font-weight:600}.compact-facts{margin-top:8px}.compact-facts>div{grid-template-columns:100px minmax(0,1fr);padding:10px 0}.actions{display:flex;justify-content:flex-end;align-items:center;gap:12px;margin-top:28px}.button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 17px;border:1px solid transparent;border-radius:3px;text-decoration:none;font-weight:700;cursor:pointer}.button.primary{background:var(--blue);color:#fff}.button.primary:hover{background:#124daF}.button.quiet{color:var(--ink);border-color:var(--line);background:#fff}.button.danger{color:#fff;background:var(--red)}.button:focus-visible,input:focus-visible,.text-button:focus-visible,.text-link:focus-visible{outline:3px solid #8bb4ff;outline-offset:3px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.span-2{grid-column:1/-1}.field label,.delete-confirm label{display:block;margin-bottom:8px;font-size:13px;font-weight:750}.field input,.delete-confirm input{width:100%;height:48px;padding:0 13px;border:1px solid #b9c4d4;border-radius:3px;background:#fff;color:var(--ink)}.field input:hover,.delete-confirm input:hover{border-color:#7f91aa}.hint{margin:7px 0 0;color:var(--muted);font-size:12px;line-height:1.5}.secret-state{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:18px;background:var(--green-soft);border-left:3px solid var(--green)}.secret-state strong,.secret-state span{display:block}.secret-state span{margin-top:4px;color:#456359;font-size:12px}.text-link{color:var(--blue);font-weight:700;text-underline-offset:3px}.readonly{margin-top:4px;padding:20px;background:#f7f9fc;border:1px solid var(--line)}code{font-family:Consolas,"Cascadia Mono",monospace;font-size:.92em;overflow-wrap:anywhere}.notice{margin:0 0 20px;padding:15px 18px;border-left:3px solid}.notice.error{color:#8b2106;background:var(--red-soft);border-color:var(--red)}.validation-stamp{display:flex;gap:14px;align-items:center;padding:16px;background:var(--green-soft);border-left:3px solid var(--green)}.validation-stamp>span{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;color:#fff;background:var(--green)}.validation-stamp strong{display:block}.validation-stamp p{margin:3px 0 0;color:#456359;font-size:12px}.diff-table{width:100%;margin-top:24px;border-collapse:collapse}.diff-table th,.diff-table td{padding:14px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.diff-table thead th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.diff-table tbody th{width:140px;font-size:13px}.pill{display:inline-block;margin-right:8px;padding:4px 8px;background:var(--blue-soft);color:var(--blue);font-size:11px;font-weight:800}.danger-zone{display:flex;align-items:center;justify-content:space-between;gap:28px;margin-top:24px;padding:24px 28px;border-color:#f1c4b8;box-shadow:none}.danger-zone p:not(.eyebrow){margin:8px 0 0;color:#795548;font-size:13px}.danger-zone .eyebrow,.danger-hero .eyebrow{color:var(--red)}.danger-panel{border-top:4px solid var(--red)}.delete-confirm{margin-top:28px;padding-top:22px;border-top:1px solid var(--line)}.session-exit{margin-top:22px;text-align:right}.text-button{border:0;background:none;color:var(--muted);text-decoration:underline;text-underline-offset:3px;cursor:pointer}.result-block{max-width:760px;padding:46px}.result-block .result-mark{display:grid;place-items:center;width:54px;height:54px;margin-bottom:32px;border-radius:50%;font-size:25px}.result-block.success .result-mark{color:#fff;background:var(--green)}.result-block.deleted .result-mark{color:#fff;background:var(--ink)}.result-block p:not(.eyebrow){color:var(--muted);line-height:1.7}.result-block.error{border-top:4px solid var(--red)}footer{display:flex;justify-content:space-between;gap:20px;margin-top:42px;padding-top:20px;border-top:1px solid var(--line);color:#7c8899;font:11px/1.3 Consolas,"Cascadia Mono",monospace}@keyframes enter{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}main>*{animation:enter .28s ease-out both}@media(prefers-reduced-motion:reduce){main>*{animation:none}}@media(max-width:760px){.app-frame{display:block}.rail{position:relative;height:auto;padding:20px}.wordmark{margin-bottom:20px}.rail ol{display:grid;grid-template-columns:repeat(4,1fr);margin:0;gap:8px}.rail ol:before{left:8%;right:8%;top:8px;bottom:auto;width:auto;height:1px}.rail li{display:block;margin:0;text-align:center}.rail-node{display:block;margin:0 auto 10px}.rail li small{display:none}.binding-badge{display:none}main{padding:34px 20px 24px}.provider-head,.danger-zone,.secret-state{align-items:flex-start;flex-direction:column}.form-grid{grid-template-columns:1fr}.span-2{grid-column:1}.facts>div{grid-template-columns:1fr;gap:5px}.actions{align-items:stretch;flex-direction:column-reverse}.button{width:100%}.diff-table{display:block;overflow-x:auto}.result-block{padding:28px}footer{display:block}footer span{display:block;margin-top:5px}}
"""
