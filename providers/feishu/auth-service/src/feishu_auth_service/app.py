from __future__ import annotations

import hashlib
import hmac
import html
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_auth_service.config import Settings
from feishu_auth_service.feishu import FeishuApiError, FeishuOAuthClient, OAuthClient
from feishu_auth_service.leases import (
    DOCX_MEDIA_READ_SCOPES,
    DOCX_READ_SCOPES,
    SHEETS_READ_SCOPES,
    WIKI_NODE_READ_SCOPES,
    LocalLeaseBroker,
)
from feishu_auth_service.models import AuthResult, UserIdentity
from feishu_auth_service.profiles import LocalProfileVault, ProfileError, ProfileVault
from feishu_auth_service.state import AuthResultStore, OAuthStateStore, StateStatus
from feishu_protocol import LeaseRequest

LOGGER = logging.getLogger("feishu_auth_service")


def create_app(
    settings: Settings,
    oauth_client: OAuthClient | None = None,
    profile_vault: ProfileVault | None = None,
    lease_broker: LocalLeaseBroker | None = None,
) -> FastAPI:
    state_store = OAuthStateStore(settings.state_ttl_seconds)
    result_store = AuthResultStore(settings.result_ttl_seconds)
    client = oauth_client or FeishuOAuthClient(settings)
    vault = profile_vault or LocalProfileVault.default()
    broker = lease_broker or LocalLeaseBroker(
        settings=settings,
        profile_vault=vault,
        token_refresher=client,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        state_store.clear()
        result_store.clear()
        broker.clear()
        await client.aclose()

    app = FastAPI(
        title="Workspace Feishu OAuth",
        version="0.4.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.state_store = state_store
    app.state.result_store = result_store
    app.state.profile_vault = vault
    app.state.lease_broker = broker

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ready",
            "mode": "local_provider_v0",
            "persistent_access_tokens": False,
            "encrypted_refresh_token_profiles": True,
        }

    @app.get("/status")
    async def status() -> dict[str, object]:
        return {
            "status": "ready",
            "configuration": settings.safe_status(),
            "pending_oauth_states": state_store.pending_count(),
            "visible_results": result_store.count(),
            "authorized_profiles": len(vault.summaries()),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        tenant_mode = (
            "已配置租户准入；授权成功后会进行精确比对。"
            if settings.allowed_tenant_key
            else "尚未配置准入租户；本次只发现 tenant_key，不建立 Profile。"
        )
        content = _page(
            "飞书 OAuth 最小纵切",
            f"""
            <p>{html.escape(tenant_mode)}</p>
            <p>回调地址：<code>{html.escape(settings.redirect_uri)}</code></p>
            <p>请求权限：<code>{html.escape(' '.join(settings.scopes))}</code></p>
            <p><a class="button" href="/oauth/start">开始飞书授权</a></p>
            <p class="muted">Access Token 仅驻进程内存；Refresh Token 由当前 Windows 用户 DPAPI 加密保存。</p>
            """,
        )
        return HTMLResponse(content)

    @app.get("/oauth/start")
    async def oauth_start() -> RedirectResponse:
        state, record = state_store.create()
        LOGGER.info("oauth_start request_ref=%s", record.request_ref)
        return RedirectResponse(client.authorization_url(state), status_code=303)

    @app.get("/callback")
    async def oauth_callback(
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> Response:
        del error_description
        consumption = state_store.consume(state)
        if consumption.status is not StateStatus.VALID or consumption.record is None:
            LOGGER.warning("oauth_callback_rejected state_status=%s", consumption.status)
            return _error_page(
                "OAuth 状态无效",
                f"state_{consumption.status.value}",
                status_code=400,
            )

        request_ref = consumption.record.request_ref
        if error is not None:
            LOGGER.info("oauth_denied request_ref=%s", request_ref)
            return _error_page("用户未完成授权", "oauth_denied", status_code=400)
        if not code:
            LOGGER.warning("oauth_callback_missing_code request_ref=%s", request_ref)
            return _error_page("授权回调缺少 Code", "oauth_code_missing", status_code=400)

        try:
            grant = await client.exchange_code(code)
            identity = await client.get_user_info(grant.access_token)
        except FeishuApiError as exc:
            LOGGER.warning(
                "oauth_provider_error request_ref=%s operation=%s http_status=%s platform_code=%s",
                request_ref,
                exc.operation,
                exc.http_status,
                exc.platform_code,
            )
            return _error_page("飞书授权验证失败", "provider_auth_failed", status_code=502)

        result = _evaluate_identity(settings, identity)
        if result.status == "tenant_not_allowed":
            del grant
            LOGGER.warning("tenant_not_allowed request_ref=%s", request_ref)
            return _error_page("当前飞书租户未获准入", result.status, status_code=403)

        if result.status == "verified":
            if result.profile_ref is None or grant.refresh_token is None:
                del grant
                LOGGER.warning("profile_authorization_missing request_ref=%s", request_ref)
                return _error_page(
                    "飞书授权缺少可刷新凭证",
                    "refresh_token_missing",
                    status_code=502,
                )
            if not set(grant.scopes).intersection(DOCX_READ_SCOPES):
                del grant
                LOGGER.warning("docx_scope_missing request_ref=%s", request_ref)
                return _error_page(
                    "飞书授权未包含 Docx 读取权限",
                    "docx_scope_missing",
                    status_code=403,
                )
            if not set(grant.scopes).intersection(WIKI_NODE_READ_SCOPES):
                del grant
                LOGGER.warning("wiki_scope_missing request_ref=%s", request_ref)
                return _error_page(
                    "飞书授权未包含 Wiki 节点读取权限",
                    "wiki_scope_missing",
                    status_code=403,
                )
            if not set(grant.scopes).intersection(DOCX_MEDIA_READ_SCOPES):
                del grant
                LOGGER.warning("docx_media_scope_missing request_ref=%s", request_ref)
                return _error_page(
                    "飞书授权未包含文档图片和附件下载权限",
                    "docx_media_scope_missing",
                    status_code=403,
                )
            if not set(grant.scopes).intersection(SHEETS_READ_SCOPES):
                del grant
                LOGGER.warning("sheets_scope_missing request_ref=%s", request_ref)
                return _error_page(
                    "飞书授权未包含电子表格读取权限",
                    "sheets_scope_missing",
                    status_code=403,
                )
            try:
                vault.save_authorization(
                    profile_ref=result.profile_ref,
                    app_id=settings.app_id,
                    tenant_key=identity.tenant_key,
                    open_id=identity.open_id,
                    union_id=identity.union_id,
                    refresh_token=grant.refresh_token,
                    refresh_token_expires_in=grant.refresh_token_expires_in,
                    scopes=grant.scopes,
                )
            except ProfileError as exc:
                del grant
                LOGGER.error(
                    "profile_persistence_failed request_ref=%s type=%s",
                    request_ref,
                    type(exc).__name__,
                )
                return _error_page(
                    "飞书 Profile 无法安全保存",
                    "profile_persistence_failed",
                    status_code=500,
                )
        del grant

        result_ref = result_store.put(result)
        LOGGER.info("oauth_result request_ref=%s status=%s", request_ref, result.status)
        return RedirectResponse(f"/oauth/result/{result_ref}", status_code=303)

    @app.get("/oauth/result/{result_ref}", response_class=HTMLResponse)
    async def oauth_result(result_ref: str) -> HTMLResponse:
        result = result_store.get(result_ref)
        if result is None:
            return _error_page("授权结果不存在或已过期", "result_not_found", status_code=404)
        rows = [
            ("状态", result.status),
            ("说明", result.message),
        ]
        if result.tenant_key:
            rows.append(("tenant_key", result.tenant_key))
        if result.profile_ref:
            rows.append(("profile_ref", result.profile_ref))
        if result.open_id_hint:
            rows.append(("open_id", result.open_id_hint))
        if result.display_name:
            rows.append(("用户", result.display_name))
        table = "".join(
            f"<tr><th>{html.escape(label)}</th><td><code>{html.escape(value)}</code></td></tr>"
            for label, value in rows
        )
        return HTMLResponse(
            _page(
                "授权验证结果",
                f"<table>{table}</table><p><a href='/'>返回首页</a></p>",
            )
        )

    @app.post("/internal/v1/token-leases")
    async def issue_token_lease(request: Request, body: LeaseRequest) -> Response:
        authorization_error = _authorize_local_client(request, settings)
        if authorization_error is not None:
            return authorization_error
        try:
            profile_ref = _select_profile_ref(
                vault,
                requested_profile_ref=body.profile_ref,
                capabilities=body.capabilities,
            )
            lease = await broker.issue(
                task_ref=body.task_ref,
                profile_ref=profile_ref,
                capabilities=body.capabilities,
            )
        except CapabilityError as exc:
            return JSONResponse(
                _capability_error_payload(exc, settings),
                status_code=_capability_http_status(exc.code),
            )
        return JSONResponse(lease.delivery().model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.error("unhandled_error type=%s", type(exc).__name__)
        return JSONResponse(
            {"status": "internal_error", "message": "Unexpected local service error"},
            status_code=500,
        )

    return app


def _authorize_local_client(request: Request, settings: Settings) -> JSONResponse | None:
    if settings.local_client_ref is None or settings.local_client_secret is None:
        error = CapabilityError(
            CapabilityErrorCode.CONFIGURATION_REQUIRED,
            "The local Provider execution client is not configured.",
        )
        return JSONResponse(error.to_payload(), status_code=503)
    client_ref = request.headers.get("X-Workspace-Client-Ref", "")
    authorization = request.headers.get("Authorization", "")
    expected_prefix = "Bearer "
    if not authorization.startswith(expected_prefix):
        return _client_unauthorized()
    supplied_secret = authorization[len(expected_prefix) :]
    if not hmac.compare_digest(client_ref, settings.local_client_ref) or not hmac.compare_digest(
        supplied_secret,
        settings.local_client_secret,
    ):
        return _client_unauthorized()
    return None


def _client_unauthorized() -> JSONResponse:
    error = CapabilityError(
        CapabilityErrorCode.CLIENT_UNAUTHORIZED,
        "The local Provider execution client could not be authenticated.",
    )
    response = JSONResponse(error.to_payload(), status_code=401)
    response.headers["WWW-Authenticate"] = "Bearer"
    return response


def _capability_http_status(code: CapabilityErrorCode) -> int:
    return {
        CapabilityErrorCode.AUTH_REQUIRED: 401,
        CapabilityErrorCode.PERMISSION_DENIED: 403,
        CapabilityErrorCode.RESOURCE_NOT_FOUND: 404,
        CapabilityErrorCode.RATE_LIMITED: 429,
        CapabilityErrorCode.PROVIDER_UNAVAILABLE: 503,
        CapabilityErrorCode.CONFIGURATION_REQUIRED: 503,
    }.get(code, 409)


def _select_profile_ref(
    vault: ProfileVault,
    *,
    requested_profile_ref: str | None,
    capabilities: tuple[str, ...],
) -> str:
    if requested_profile_ref is not None:
        return requested_profile_ref
    try:
        summaries = vault.summaries()
    except ProfileError as exc:
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_UNAVAILABLE,
            "The local Feishu Profile store is unavailable.",
            retryable=True,
        ) from exc
    if not summaries:
        raise CapabilityError(
            CapabilityErrorCode.AUTH_REQUIRED,
            "No authorized Feishu Profile is available on this machine.",
            details={"capabilities": list(capabilities)},
        )
    if len(summaries) > 1:
        raise CapabilityError(
            CapabilityErrorCode.PROFILE_SELECTION_REQUIRED,
            "More than one authorized Feishu Profile is available.",
            details={
                "profile_refs": [summary.profile_ref for summary in summaries],
            },
        )
    return summaries[0].profile_ref


def _capability_error_payload(
    error: CapabilityError,
    settings: Settings,
) -> dict[str, object]:
    payload = error.to_payload()
    if error.code is CapabilityErrorCode.AUTH_REQUIRED:
        details = dict(error.details)
        details["authorization_url"] = settings.authorization_url
        payload["details"] = details
    return payload


def _evaluate_identity(settings: Settings, identity: UserIdentity) -> AuthResult:
    if settings.allowed_tenant_key is None:
        return AuthResult(
            status="tenant_discovered",
            message="已回读飞书租户；请配置准入 tenant_key 后重新授权。",
            tenant_key=identity.tenant_key,
            open_id_hint=_mask(identity.open_id),
            display_name=identity.name,
        )
    if not hmac.compare_digest(identity.tenant_key, settings.allowed_tenant_key):
        return AuthResult(
            status="tenant_not_allowed",
            message="飞书返回的租户与准入租户不一致。",
        )
    return AuthResult(
        status="verified",
        message="真实 OAuth、租户校验和用户身份只读回读均已完成。",
        tenant_key=identity.tenant_key,
        profile_ref=_profile_ref(settings.app_id, identity),
        open_id_hint=_mask(identity.open_id),
        display_name=identity.name,
    )


def _profile_ref(app_id: str, identity: UserIdentity) -> str:
    source = f"{app_id}\x1f{identity.tenant_key}\x1f{identity.open_id}".encode()
    return f"profile_{hashlib.sha256(source).hexdigest()[:20]}"


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


def _error_page(title: str, status: str, *, status_code: int) -> HTMLResponse:
    content = _page(
        title,
        f"<p>状态：<code>{html.escape(status)}</code></p><p><a href='/'>返回首页</a></p>",
    )
    return HTMLResponse(content, status_code=status_code)


def _page(title: str, body: str) -> str:
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 64px auto; padding: 0 24px; color: #172033; }}
    h1 {{ font-size: 28px; }}
    .button {{ display: inline-block; padding: 10px 16px; border-radius: 8px; background: #1456f0; color: white; text-decoration: none; }}
    .muted {{ color: #667085; }}
    code {{ overflow-wrap: anywhere; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #e4e7ec; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ width: 160px; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  {body}
</body>
</html>"""
