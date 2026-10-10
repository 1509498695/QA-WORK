from __future__ import annotations

import hmac
import secrets
from importlib.resources import files
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from qa_skill_hub_control_plane.identity import (
    FeishuIdentityGateway,
    IdentityGateway,
    IdentityGatewayError,
)
from qa_skill_hub_control_plane.models import ProviderStatus, ViewerState
from qa_skill_hub_control_plane.pages import (
    render_account,
    render_bootstrap,
    render_error,
    render_join,
    render_platform,
    render_reset_complete,
    render_start,
    render_team,
)
from qa_skill_hub_control_plane.settings import ControlPlaneSettings
from qa_skill_hub_control_plane.store import ControlPlaneStateError, ControlPlaneStore


SESSION_COOKIE = "qa_skill_hub_session"
_STYLES = files("qa_skill_hub_control_plane").joinpath("assets/control-plane.css").read_text(
    encoding="utf-8"
)


def create_app(
    settings: ControlPlaneSettings | None = None,
    *,
    store: ControlPlaneStore | None = None,
    identity_gateway: IdentityGateway | None = None,
    bootstrap_token: str | None = None,
) -> FastAPI:
    resolved_settings = settings or ControlPlaneSettings.default()
    state = store or ControlPlaneStore(resolved_settings.database_path)
    state.initialize()
    gateway = identity_gateway or FeishuIdentityGateway(resolved_settings)
    public_form_token = secrets.token_urlsafe(32)

    app = FastAPI(
        title="QA Skill Hub Control Plane",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved_settings
    app.state.store = state
    app.state.identity_gateway = gateway
    app.state.bootstrap_token_available = bootstrap_token is not None

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
            "default-src 'none'; style-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ready",
            "schema_version": 1,
            "platform_bootstrapped": state.has_platform_admins(),
        }

    @app.get("/", include_in_schema=False)
    async def root(request: Request) -> RedirectResponse:
        return RedirectResponse(
            "/account" if _viewer(request, state) else "/start",
            status_code=303,
        )

    @app.get("/assets/control-plane.css", response_class=Response)
    async def stylesheet() -> Response:
        return Response(_STYLES, media_type="text/css; charset=utf-8")

    @app.get("/start", response_class=HTMLResponse)
    async def start(request: Request) -> HTMLResponse:
        intent = request.query_params.get("intent", "")
        if intent not in {"", "join", "create"}:
            intent = ""
        return HTMLResponse(
            render_start(
                intent=intent,
                viewer=_viewer(request, state),
                platform_bootstrapped=state.has_platform_admins(),
                local_bootstrap_available=bootstrap_token is not None,
                public_form_token=public_form_token,
            )
        )

    @app.get("/bootstrap/local")
    async def local_bootstrap() -> Response:
        if state.has_platform_admins():
            return RedirectResponse("/auth/feishu/start?intent=account", status_code=303)
        if bootstrap_token is None:
            return _state_error(
                ControlPlaneStateError(
                    "bootstrap_unavailable",
                    "当前进程没有可用的初始化凭证，请重新启动控制面。",
                )
            )
        try:
            login_state, _, challenge = state.begin_login(
                intent="bootstrap",
                ttl_seconds=resolved_settings.login_ttl_seconds,
                bootstrap_token=bootstrap_token,
            )
        except ControlPlaneStateError as exc:
            return _state_error(exc)
        return RedirectResponse(
            gateway.authorization_url(
                state=login_state,
                code_challenge=challenge,
                return_to=resolved_settings.callback_uri,
            ),
            status_code=303,
        )

    @app.get("/bootstrap/{token}", response_class=HTMLResponse)
    async def bootstrap(token: str) -> HTMLResponse:
        if not state.bootstrap_is_valid(token):
            return HTMLResponse(
                render_error(
                    title="Bootstrap 链接不可用",
                    message="链接无效、已过期或 Bootstrap 已完成。请重新启动控制面生成新链接。",
                    status="bootstrap_invalid",
                ),
                status_code=400,
            )
        return HTMLResponse(render_bootstrap(token=token))

    @app.get("/auth/feishu/start")
    async def auth_start(request: Request) -> Response:
        intent = request.query_params.get("intent", "account")
        invite_token = request.query_params.get("invite")
        bootstrap_token = request.query_params.get("bootstrap")
        try:
            login_state, _, challenge = state.begin_login(
                intent=intent,
                ttl_seconds=resolved_settings.login_ttl_seconds,
                invite_token=invite_token,
                bootstrap_token=bootstrap_token,
            )
        except ControlPlaneStateError as exc:
            return _state_error(exc, viewer=_viewer(request, state))
        location = gateway.authorization_url(
            state=login_state,
            code_challenge=challenge,
            return_to=resolved_settings.callback_uri,
        )
        return RedirectResponse(location, status_code=303)

    @app.get("/auth/feishu/callback")
    async def auth_callback(request: Request) -> Response:
        try:
            login = state.consume_login(request.query_params.get("state"))
            ticket = request.query_params.get("ticket")
            if not ticket:
                raise ControlPlaneStateError(
                    "identity_ticket_missing",
                    "飞书身份回调缺少一次性票据。",
                )
            identity = await gateway.redeem(
                ticket=ticket,
                code_verifier=login.code_verifier,
            )
            user = state.complete_identity(identity, login)
            session_token, _ = state.create_session(
                user.user_ref,
                ttl_seconds=resolved_settings.session_ttl_seconds,
            )
            if login.intent == "join" and login.invite_token:
                viewer = state.viewer(session_token)
                assert viewer is not None
                if viewer.membership is None:
                    state.join_team_by_invite(
                        actor_user_ref=user.user_ref,
                        token=login.invite_token,
                    )
                    location = "/account"
                elif viewer.team is not None and state.invitation_target(
                    login.invite_token
                ).team_ref == viewer.team.team_ref:
                    location = "/account"
                else:
                    location = f"/join/{login.invite_token}"
            elif login.intent in {"bootstrap", "create"}:
                location = "/start?intent=create"
            else:
                location = "/account"
            response = RedirectResponse(location, status_code=303)
            _set_session_cookie(response, session_token, resolved_settings)
            return response
        except IdentityGatewayError as exc:
            return HTMLResponse(
                render_error(
                    title="飞书身份无法建立",
                    message=str(exc),
                    status="identity_gateway_failed",
                ),
                status_code=502,
            )
        except ControlPlaneStateError as exc:
            return _state_error(exc)

    @app.get("/account", response_class=HTMLResponse)
    async def account(request: Request) -> Response:
        viewer = _viewer(request, state)
        if viewer is None:
            return _login_entry_redirect(state)
        provider = await gateway.status()
        return HTMLResponse(
            render_account(
                viewer=viewer,
                provider=provider,
                edit_name=request.query_params.get("edit") == "name",
            )
        )

    @app.post("/account/display-name")
    async def update_display_name(request: Request) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        assert viewer is not None and form is not None
        try:
            state.update_display_name(
                actor_user_ref=viewer.user.user_ref,
                display_name=_form_text(form, "display_name"),
            )
        except ControlPlaneStateError as exc:
            return HTMLResponse(
                render_account(
                    viewer=viewer,
                    provider=await gateway.status(),
                    edit_name=True,
                    error=str(exc),
                ),
                status_code=400,
            )
        return RedirectResponse("/account", status_code=303)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        viewer, _, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        assert viewer is not None
        state.delete_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/start", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/join/resolve")
    async def resolve_invite(request: Request) -> Response:
        form = await request.form()
        if not hmac.compare_digest(
            _form_text(form, "public_csrf"),
            public_form_token,
        ) or not _optional_origin_allowed(request, resolved_settings):
            return _forbidden("请求来源无效。")
        invite_url = _form_text(form, "invite_url")
        try:
            token = _invite_token_from_url(invite_url, resolved_settings.public_origin)
            state.invitation_target(token)
        except (ControlPlaneStateError, ValueError) as exc:
            message = str(exc) if str(exc) else "邀请链接无效。"
            return HTMLResponse(
                render_start(
                    intent="join",
                    viewer=_viewer(request, state),
                    platform_bootstrapped=state.has_platform_admins(),
                    public_form_token=public_form_token,
                    error=message,
                ),
                status_code=400,
            )
        return RedirectResponse(f"/join/{token}", status_code=303)

    @app.get("/join/{token}", response_class=HTMLResponse)
    async def join_preview(request: Request, token: str) -> Response:
        try:
            team = state.invitation_target(token)
        except ControlPlaneStateError as exc:
            return _state_error(exc, viewer=_viewer(request, state))
        return HTMLResponse(
            render_join(team=team, token=token, viewer=_viewer(request, state))
        )

    @app.post("/join/{token}")
    async def join_team(request: Request, token: str) -> Response:
        viewer, _, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        assert viewer is not None
        try:
            state.join_team_by_invite(actor_user_ref=viewer.user.user_ref, token=token)
        except ControlPlaneStateError as exc:
            try:
                team = state.invitation_target(token)
            except ControlPlaneStateError:
                return _state_error(exc, viewer=viewer)
            return HTMLResponse(
                render_join(team=team, token=token, viewer=viewer, error=str(exc)),
                status_code=409,
            )
        return RedirectResponse("/account", status_code=303)

    @app.post("/teams")
    async def create_team(request: Request) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        assert viewer is not None and form is not None
        try:
            state.create_team(
                actor_user_ref=viewer.user.user_ref,
                name=_form_text(form, "name"),
            )
        except ControlPlaneStateError as exc:
            return HTMLResponse(
                render_start(
                    intent="create",
                    viewer=viewer,
                    platform_bootstrapped=True,
                    error=str(exc),
                ),
                status_code=409,
            )
        return RedirectResponse("/team", status_code=303)

    @app.get("/team", response_class=HTMLResponse)
    async def team(request: Request) -> Response:
        viewer = _viewer(request, state)
        if viewer is None:
            return _login_entry_redirect(state)
        access_error = _require_team_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None and viewer.team is not None
        return HTMLResponse(
            render_team(
                viewer=viewer,
                members=state.list_team_members(viewer.team.team_ref),
                invites=state.list_invites(viewer.team.team_ref),
                public_origin=resolved_settings.public_origin,
                invite_drawer=request.query_params.get("invite") == "1",
            )
        )

    @app.post("/team/invites")
    async def create_invite(request: Request) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_team_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None and viewer.team is not None and form is not None
        try:
            token, _ = state.create_invite(
                actor_user_ref=viewer.user.user_ref,
                expires_in_seconds=int(_form_text(form, "expires_in")),
                max_uses=int(_form_text(form, "max_uses")),
            )
        except (ControlPlaneStateError, ValueError) as exc:
            return HTMLResponse(
                render_team(
                    viewer=viewer,
                    members=state.list_team_members(viewer.team.team_ref),
                    invites=state.list_invites(viewer.team.team_ref),
                    public_origin=resolved_settings.public_origin,
                    invite_drawer=True,
                    error=str(exc),
                ),
                status_code=400,
            )
        return HTMLResponse(
            render_team(
                viewer=viewer,
                members=state.list_team_members(viewer.team.team_ref),
                invites=state.list_invites(viewer.team.team_ref),
                public_origin=resolved_settings.public_origin,
                created_invite_token=token,
            ),
            status_code=201,
        )

    @app.post("/team/invites/{invite_ref}/revoke")
    async def revoke_invite(request: Request, invite_ref: str) -> Response:
        viewer, _, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_team_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None
        try:
            state.revoke_invite(
                actor_user_ref=viewer.user.user_ref,
                invite_ref=invite_ref,
            )
        except ControlPlaneStateError as exc:
            return _state_error(exc, viewer=viewer)
        return RedirectResponse("/team", status_code=303)

    @app.post("/team/members/{user_ref}/role")
    async def set_team_role(request: Request, user_ref: str) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_team_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None and form is not None
        try:
            state.set_team_role(
                actor_user_ref=viewer.user.user_ref,
                target_user_ref=user_ref,
                role=_form_text(form, "role"),
            )
        except ControlPlaneStateError as exc:
            return _state_error(exc, viewer=viewer)
        return RedirectResponse("/team", status_code=303)

    @app.get("/platform", response_class=HTMLResponse)
    async def platform(request: Request) -> Response:
        viewer = _viewer(request, state)
        if viewer is None:
            return _login_entry_redirect(state)
        access_error = _require_platform_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None
        section = request.query_params.get("section", "overview")
        if section not in {"overview", "feishu", "admins", "reset"}:
            section = "overview"
        return HTMLResponse(
            render_platform(
                viewer=viewer,
                section=section,
                provider=await gateway.status(),
                admins=state.list_platform_admins(),
                counts=state.counts(),
            )
        )

    @app.post("/platform/admins")
    async def grant_platform_admin(request: Request) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_platform_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None and form is not None
        try:
            state.grant_platform_admin(
                actor_user_ref=viewer.user.user_ref,
                target_user_ref=_form_text(form, "user_ref"),
            )
        except ControlPlaneStateError as exc:
            return await _platform_error_page(viewer, exc, state, gateway)
        return RedirectResponse("/platform?section=admins", status_code=303)

    @app.post("/platform/admins/{user_ref}/revoke")
    async def revoke_platform_admin(request: Request, user_ref: str) -> Response:
        viewer, _, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_platform_admin(viewer)
        if access_error is not None:
            return access_error
        assert viewer is not None
        try:
            state.revoke_platform_admin(
                actor_user_ref=viewer.user.user_ref,
                target_user_ref=user_ref,
            )
        except ControlPlaneStateError as exc:
            return await _platform_error_page(viewer, exc, state, gateway)
        return RedirectResponse("/platform?section=admins", status_code=303)

    @app.post("/platform/reset")
    async def reset_platform(request: Request) -> Response:
        viewer, form, error = await _authorized_form(request, state, resolved_settings)
        if error is not None:
            return error
        access_error = _require_platform_admin(viewer)
        if access_error is not None:
            return access_error
        assert form is not None
        if not hmac.compare_digest(_form_text(form, "confirmation"), "RESET"):
            assert viewer is not None
            return HTMLResponse(
                render_platform(
                    viewer=viewer,
                    section="reset",
                    provider=await gateway.status(),
                    admins=state.list_platform_admins(),
                    counts=state.counts(),
                    error="请输入完全一致的 RESET。",
                ),
                status_code=400,
            )
        bootstrap_token = state.reset_and_issue_bootstrap(
            ttl_seconds=resolved_settings.bootstrap_ttl_seconds
        )
        response = HTMLResponse(
            render_reset_complete(
                bootstrap_url=f"{resolved_settings.public_origin}/bootstrap/{bootstrap_token}"
            )
        )
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> Response:
        del exc
        return HTMLResponse(
            render_error(
                title="中央服务发生意外错误",
                message="请查看服务日志后重试。页面不会显示内部异常或凭证。",
                status="internal_error",
                viewer=_viewer(request, state),
            ),
            status_code=500,
        )

    return app


def _viewer(request: Request, store: ControlPlaneStore) -> ViewerState | None:
    return store.viewer(request.cookies.get(SESSION_COOKIE))


def _login_entry_redirect(store: ControlPlaneStore) -> RedirectResponse:
    location = "/auth/feishu/start?intent=account" if store.has_platform_admins() else "/start"
    return RedirectResponse(location, status_code=303)


def _set_session_cookie(
    response: Response,
    token: str,
    settings: ControlPlaneSettings,
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _authorized_form(
    request: Request,
    store: ControlPlaneStore,
    settings: ControlPlaneSettings,
) -> tuple[ViewerState | None, object | None, Response | None]:
    viewer = _viewer(request, store)
    if viewer is None:
        return None, None, _forbidden("请先登录。", status_code=401)
    if not _optional_origin_allowed(request, settings):
        return viewer, None, _forbidden("请求来源无效。")
    form = await request.form()
    csrf = _form_text(form, "csrf")
    if not hmac.compare_digest(csrf, viewer.csrf_token):
        return viewer, None, _forbidden("请求验证失败，请刷新页面后重试。")
    return viewer, form, None


def _origin_allowed(request: Request, settings: ControlPlaneSettings) -> bool:
    origin = request.headers.get("Origin", "")
    supplied = urlsplit(origin)
    expected = urlsplit(settings.public_origin)
    loopback_hosts = {"localhost", "127.0.0.1"}
    return (
        supplied.scheme == expected.scheme
        and supplied.hostname in loopback_hosts
        and expected.hostname in loopback_hosts
        and supplied.port == expected.port
        and supplied.path in {"", "/"}
        and not supplied.query
        and not supplied.fragment
    )


def _optional_origin_allowed(request: Request, settings: ControlPlaneSettings) -> bool:
    origin = request.headers.get("Origin")
    return origin in {None, "null"} or _origin_allowed(request, settings)


def _form_text(form: object, key: str) -> str:
    getter = getattr(form, "get", None)
    if getter is None:
        return ""
    value = getter(key, "")
    return value.strip() if isinstance(value, str) else ""


def _invite_token_from_url(value: str, public_origin: str) -> str:
    parsed = urlsplit(value)
    expected = urlsplit(public_origin)
    if (
        parsed.scheme != expected.scheme
        or parsed.netloc != expected.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("邀请链接不属于当前中央服务。")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "join" or len(parts[1]) < 32:
        raise ValueError("邀请链接格式无效。")
    return parts[1]


def _require_team_admin(viewer: ViewerState | None) -> Response | None:
    if viewer is None:
        return _forbidden("请先登录。", status_code=401)
    if not viewer.is_team_admin or viewer.team is None:
        return _forbidden("需要团队管理员权限。")
    return None


def _require_platform_admin(viewer: ViewerState | None) -> Response | None:
    if viewer is None:
        return _forbidden("请先登录。", status_code=401)
    if not viewer.is_platform_admin:
        return _forbidden("需要平台管理员权限。")
    return None


def _forbidden(message: str, *, status_code: int = 403) -> HTMLResponse:
    return HTMLResponse(
        render_error(
            title="无法执行当前操作",
            message=message,
            status="permission_denied" if status_code == 403 else "authentication_required",
        ),
        status_code=status_code,
    )


def _state_error(
    error: ControlPlaneStateError,
    *,
    viewer: ViewerState | None = None,
) -> HTMLResponse:
    status_code = 400 if error.code.endswith(("invalid", "expired", "required")) else 409
    if error.code.endswith("_required"):
        status_code = 403
    return HTMLResponse(
        render_error(
            title="当前状态无法继续",
            message=str(error),
            status=error.code,
            viewer=viewer,
        ),
        status_code=status_code,
    )


async def _platform_error_page(
    viewer: ViewerState,
    error: ControlPlaneStateError,
    store: ControlPlaneStore,
    gateway: IdentityGateway,
) -> HTMLResponse:
    return HTMLResponse(
        render_platform(
            viewer=viewer,
            section="admins",
            provider=await gateway.status(),
            admins=store.list_platform_admins(),
            counts=store.counts(),
            error=str(error),
        ),
        status_code=409,
    )


app = create_app()
