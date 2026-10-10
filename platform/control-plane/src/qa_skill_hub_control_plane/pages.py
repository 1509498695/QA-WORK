from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from qa_skill_hub_control_plane.models import (
    InviteRecord,
    PlatformAdminRecord,
    ProviderStatus,
    TeamMemberRecord,
    TeamRecord,
    ViewerState,
)


@dataclass(frozen=True)
class Viewer:
    display_name: str
    initials: str
    team_admin: bool = False
    platform_admin: bool = False


@dataclass(frozen=True)
class ReadinessStep:
    label: str
    state: str


_NAV_ITEMS = (
    ("account", "团队与账号", "/account"),
    ("team", "团队管理", "/team"),
    ("platform", "平台设置", "/platform?section=overview"),
)


def visible_navigation(viewer: Viewer) -> tuple[str, ...]:
    items = ["account"]
    if viewer.team_admin:
        items.append("team")
    if viewer.platform_admin:
        items.append("platform")
    return tuple(items)


def render_start(
    *,
    intent: str = "",
    viewer: ViewerState | None = None,
    platform_bootstrapped: bool,
    local_bootstrap_available: bool = False,
    public_form_token: str | None = None,
    error: str | None = None,
) -> str:
    page_viewer = _viewer(viewer)
    if not platform_bootstrapped:
        bootstrap_action = (
            """<a class="button primary" href="/bootstrap/local">使用飞书账号初始化并登录</a>"""
            if local_bootstrap_available
            else """<p class="form-notice">当前进程没有可用的初始化凭证，请从启动终端重新运行控制面。</p>"""
        )
        body = f"""
        <section class="start-focus compact-focus">
          <p class="context-label">首次启动保护</p>
          <h1>初始化平台管理员</h1>
          <p class="page-lede">使用你的飞书企业账号验证身份，并建立当前中央服务的首位平台管理员。</p>
          {_notice(error) if error else ''}
          <div class="oauth-explainer"><span class="identity-mark" aria-hidden="true">飞</span><div><strong>一次验证，完成初始化</strong><p>初始化凭证只保存在本机服务内存，不会显示在页面或发送给浏览器。</p></div></div>
          {bootstrap_action}
        </section>
        """
    elif intent == "create":
        if viewer is None:
            action = '<a class="button primary" href="/auth/feishu/start?intent=create">继续飞书登录</a>'
        elif viewer.membership is not None:
            action = '<a class="button primary" href="/account">查看当前团队</a>'
        else:
            action = f"""
            <form class="start-form" method="post" action="/teams">
              {_csrf(viewer.csrf_token)}
              <label class="field"><span>团队名称</span><input name="name" maxlength="80" autocomplete="organization" required placeholder="例如：SAMO QA"></label>
              <button class="button primary wide" type="submit">创建团队</button>
            </form>
            """
        body = f"""
        <section class="start-focus compact-focus">
          <a class="back-link" href="/start">← 返回选择</a>
          <p class="context-label">内部 QA 首次使用</p>
          <h1>创建团队</h1>
          <p class="page-lede">创建者自动成为该团队的第一名团队管理员，不会因此成为平台管理员。</p>
          {_notice(error) if error else ''}
          {action}
        </section>
        """
    elif intent == "join":
        body = f"""
        <section class="start-focus compact-focus">
          <a class="back-link" href="/start">← 返回选择</a>
          <p class="context-label">内部 QA 首次使用</p>
          <h1>加入已有团队</h1>
          <p class="page-lede">粘贴团队管理员发给你的完整邀请链接。系统会先验证邀请，再进入飞书登录。</p>
          {_notice(error) if error else ''}
          <form class="start-form" method="post" action="/join/resolve">
            {_public_csrf(public_form_token or '')}
            <label class="field"><span>团队邀请链接</span><input name="invite_url" type="url" required placeholder="http://127.0.0.1:3100/join/..."></label>
            <button class="button primary wide" type="submit">检查邀请</button>
          </form>
        </section>
        """
    else:
        body = f"""
        <section class="start-focus">
          <p class="context-label">内部 QA 首次使用</p>
          <h1>{'继续设置你的工作区' if viewer else '你准备怎么开始？'}</h1>
          <p class="page-lede">一个账号在当前中央服务中只属于一个团队。</p>
          {_notice(error) if error else ''}
          <div class="task-choices" aria-label="首次使用方式">
            <a class="task-choice primary-choice" href="/start?intent=join"><span class="choice-icon" aria-hidden="true">人</span><span><strong>加入已有团队</strong><small>使用团队管理员发给你的邀请链接</small></span><span class="choice-arrow" aria-hidden="true">→</span></a>
            <a class="task-choice" href="/start?intent=create"><span class="choice-icon neutral" aria-hidden="true">＋</span><span><strong>创建团队</strong><small>在当前中央服务中建立新的 QA 团队</small></span><span class="choice-arrow" aria-hidden="true">→</span></a>
          </div>
          <p class="trust-note"><span aria-hidden="true">◇</span> 使用飞书企业账号验证身份，无需注册新账号</p>
        </section>
        """
    return _shell(title="首次使用", active="", body=body, steps=_readiness(viewer, provider_ready=False), viewer=page_viewer)


def render_bootstrap(*, token: str) -> str:
    body = f"""
    <section class="start-focus compact-focus">
      <p class="context-label">一次性 Bootstrap</p><h1>建立首位平台管理员</h1>
      <p class="page-lede">飞书验证成功后，你会成为当前中央服务的第一名平台管理员。该权限不自动加入任何团队。</p>
      <div class="oauth-explainer"><span class="identity-mark" aria-hidden="true">飞</span><div><strong>使用飞书企业账号继续</strong><p>身份由 Feishu Provider 回读，控制面不会接收 OAuth Token。</p></div></div>
      <a class="button primary" href="/auth/feishu/start?intent=bootstrap&amp;bootstrap={escape(token, quote=True)}">验证并完成 Bootstrap</a>
    </section>
    """
    return _shell(title="平台 Bootstrap", active="", body=body, steps=_readiness(None, provider_ready=False), viewer=None)


def render_account(
    *,
    viewer: ViewerState,
    provider: ProviderStatus,
    edit_name: bool = False,
    error: str | None = None,
) -> str:
    team_role = _role_label(viewer.membership.role) if viewer.membership else "尚未加入"
    team_name = viewer.team.name if viewer.team else "—"
    team_ref = f"<code>{escape(viewer.team.team_ref)}</code>" if viewer.team else "—"
    if edit_name:
        name_control = f"""
        <form class="inline-form" method="post" action="/account/display-name">{_csrf(viewer.csrf_token)}<input name="display_name" value="{escape(viewer.user.display_name, quote=True)}" maxlength="80" required><button class="button primary" type="submit">保存</button><a class="button secondary" href="/account">取消</a></form>
        """
    else:
        name_control = escape(viewer.user.display_name)
    ready = provider.reachable and provider.configured and viewer.membership is not None
    status_panel = (
        """<section class="status-panel success"><span class="status-mark" aria-hidden="true">✓</span><div><h2>已可以开始使用</h2><p>平台身份、团队资格和 Feishu Provider 均已就绪。</p></div></section>"""
        if ready
        else _account_blocker(viewer, provider)
    )
    body = f"""
    <header class="page-heading"><div><p class="context-label">个人工作区</p><h1>团队与账号</h1></div></header>
    {_notice(error) if error else ''}{status_panel}
    <section class="detail-section"><header><h2>身份</h2>{'' if edit_name else '<a href="/account?edit=name">修改显示名称</a>'}</header>{_definition_rows((('飞书认证姓名', escape(viewer.user.verified_name or '未提供')), ('用户显示名称', name_control), ('用户标识', f'<code>{escape(viewer.user.user_ref)}</code>')))}</section>
    <section class="detail-section"><header><h2>当前团队</h2>{'<a href="/start">加入或创建团队</a>' if viewer.team is None else ''}</header>{_definition_rows((('团队名称', escape(team_name)), ('团队资格', escape(team_role)), ('团队标识', team_ref)))}</section>
    <section class="detail-section"><header><h2>飞书连接</h2><a href="/auth/feishu/start?intent=account">重新验证</a></header>{_definition_rows((('授权主体', escape(viewer.user.verified_name or viewer.user.display_name)), ('验证状态', "<span class='inline-status ready'>已验证</span>"), ('Profile', f'<code>{escape(viewer.user.profile_ref)}</code>'), ('最近验证', escape(_date(viewer.user.last_verified_at)))))}</section>
    """
    return _shell(title="团队与账号", active="account", body=body, steps=_readiness(viewer, provider_ready=provider.reachable and provider.configured), viewer=_viewer(viewer), csrf_token=viewer.csrf_token)


def render_join(
    *,
    team: TeamRecord,
    token: str,
    viewer: ViewerState | None,
    error: str | None = None,
) -> str:
    switching = viewer is not None and viewer.team is not None and viewer.team.team_ref != team.team_ref
    if viewer is None:
        action = f'<a class="button primary" href="/auth/feishu/start?intent=join&amp;invite={escape(token, quote=True)}">飞书登录并加入</a>'
    elif viewer.team is not None and viewer.team.team_ref == team.team_ref:
        action = '<a class="button primary" href="/account">你已在该团队</a>'
    else:
        warning = f'<p class="switch-warning">确认后将退出“{escape(viewer.team.name)}”并加入目标团队。平台管理员资格不受影响。</p>' if switching and viewer.team is not None else ''
        action = f"""{warning}<form method="post" action="/join/{escape(token, quote=True)}">{_csrf(viewer.csrf_token)}<button class="button primary" type="submit">{'确认切换团队' if switching else '加入团队'}</button></form>"""
    body = f"""
    <section class="start-focus compact-focus"><p class="context-label">团队邀请</p><h1>{escape(team.name)}</h1><p class="page-lede">目标团队 <code>{escape(team.team_ref)}</code> · 仅同一飞书租户身份可加入</p>{_notice(error) if error else ''}{action}</section>
    """
    return _shell(title="加入团队", active="", body=body, steps=_readiness(viewer, provider_ready=viewer is not None), viewer=_viewer(viewer), csrf_token=viewer.csrf_token if viewer else None)


def render_team(
    *,
    viewer: ViewerState,
    members: tuple[TeamMemberRecord, ...],
    invites: tuple[InviteRecord, ...],
    public_origin: str,
    invite_drawer: bool = False,
    created_invite_token: str | None = None,
    error: str | None = None,
) -> str:
    assert viewer.team is not None
    rows = "".join(_member_row(member, viewer.csrf_token) for member in members)
    invite_rows = "".join(_invite_row(invite, viewer.csrf_token) for invite in invites)
    created = ""
    if created_invite_token:
        link = f"{public_origin}/join/{created_invite_token}"
        created = f"""<section class="created-invite"><strong>邀请已创建，请现在复制</strong><p>出于安全原因，关闭页面后不会再次显示完整链接。</p><input readonly value="{escape(link, quote=True)}" aria-label="新邀请链接"></section>"""
    body = f"""
    <header class="page-heading team-heading"><div><p class="context-label">团队空间</p><h1>团队管理</h1></div><a class="button primary" href="/team?invite=1">生成邀请链接</a></header>
    {_notice(error) if error else ''}{created}
    <div class="team-summary"><span class="team-avatar" aria-hidden="true">{escape(_initials(viewer.team.name))}</span><strong>{escape(viewer.team.name)}</strong><code>{escape(viewer.team.team_ref)}</code><span>{len(members)} 名成员</span></div>
    <div class="table-frame"><table class="member-table"><thead><tr><th>成员身份</th><th>团队备注</th><th>角色</th><th>加入时间</th><th>状态</th><th>管理</th></tr></thead><tbody>{rows}</tbody></table></div>
    <section class="invite-list"><header><h2>有效邀请 <span>{len(invites)}</span></h2></header>{invite_rows or '<p class="empty-state">当前没有有效邀请。</p>'}</section>
    {_invite_drawer(viewer) if invite_drawer else ''}
    """
    return _shell(title="团队管理", active="team", body=body, steps=_readiness(viewer, provider_ready=True), viewer=_viewer(viewer), body_class="drawer-open" if invite_drawer else "", csrf_token=viewer.csrf_token)


def render_platform(
    *,
    viewer: ViewerState,
    section: str,
    provider: ProviderStatus,
    admins: tuple[PlatformAdminRecord, ...],
    counts: dict[str, int],
    error: str | None = None,
) -> str:
    local_nav = "".join(f'<a href="/platform?section={key}"{_current(key == section)}>{label}</a>' for key, label in (("overview", "服务概览"), ("feishu", "Feishu Provider"), ("admins", "平台管理员"), ("reset", "开发重置")))
    body = f"""
    <header class="page-heading"><div><p class="context-label">平台管理</p><h1>平台设置</h1><p class="page-helper">仅平台管理员可见；平台权限不授予团队业务访问。</p></div></header>
    {_notice(error) if error else ''}<div class="settings-layout"><nav class="settings-nav" aria-label="平台设置分区">{local_nav}</nav><section class="settings-workspace">{_platform_section(section, viewer, provider, admins, counts)}</section></div>
    """
    return _shell(title="平台设置", active="platform", body=body, steps=_readiness(viewer, provider_ready=provider.reachable and provider.configured), viewer=_viewer(viewer), csrf_token=viewer.csrf_token)


def render_reset_complete(*, bootstrap_url: str) -> str:
    body = f"""
    <section class="start-focus compact-focus"><p class="context-label">开发重置已完成</p><h1>当前实例已恢复为空白状态</h1><p class="page-lede">所有本地平台身份、团队、会话和审计状态已删除。飞书远端文档与 Provider 凭证未删除。</p><div class="created-invite"><strong>新的 Bootstrap 链接</strong><p>只显示这一次，请立即打开。</p><a class="button primary" href="{escape(bootstrap_url, quote=True)}">开始 Bootstrap</a></div></section>
    """
    return _shell(title="开发重置完成", active="", body=body, steps=_readiness(None, provider_ready=False), viewer=None)


def render_error(*, title: str, message: str, status: str, viewer: ViewerState | None = None) -> str:
    body = f"""<section class="start-focus compact-focus"><p class="context-label danger-text">{escape(status)}</p><h1>{escape(title)}</h1><p class="page-lede">{escape(message)}</p><a class="button primary" href="{'/account' if viewer else '/start'}">返回</a></section>"""
    return _shell(title=title, active="", body=body, steps=_readiness(viewer, provider_ready=False), viewer=_viewer(viewer), csrf_token=viewer.csrf_token if viewer else None)


def _shell(
    *,
    title: str,
    active: str,
    body: str,
    steps: tuple[ReadinessStep, ...],
    viewer: Viewer | None,
    body_class: str = "",
    csrf_token: str | None = None,
) -> str:
    navigation = ""
    identity = '<a class="help-link" href="/start">帮助</a>'
    if viewer is not None:
        allowed = set(visible_navigation(viewer))
        navigation = '<nav class="global-nav" aria-label="主导航">' + "".join(f'<a href="{href}"{_current(key == active)}>{label}</a>' for key, label, href in _NAV_ITEMS if key in allowed) + "</nav>"
        identity = f"""<span class="viewer-name">{escape(viewer.display_name)}</span><span class="viewer-avatar" aria-hidden="true">{escape(viewer.initials)}</span><form method="post" action="/logout" class="logout-form">{_csrf(csrf_token or '')}<button class="text-action" type="submit">退出</button></form>"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>{escape(title)} · QA Skill Hub</title><link rel="stylesheet" href="/assets/control-plane.css"></head>
<body class="{escape(body_class)}"><header class="topbar"><a class="brand" href="{'/account' if viewer else '/start'}">QA Skill Hub</a>{navigation}<div class="topbar-identity">{identity}</div></header><main><div class="workspace">{_readiness_strip(steps)}{body}<footer class="prototype-note">真实平台状态 · 身份由 Feishu Provider 验证 · Token 不进入控制面</footer></div></main></body></html>"""


def _viewer(viewer: ViewerState | None) -> Viewer | None:
    if viewer is None:
        return None
    return Viewer(display_name=viewer.user.display_name, initials=_initials(viewer.user.display_name), team_admin=viewer.is_team_admin, platform_admin=viewer.is_platform_admin)


def _readiness(viewer: ViewerState | None, *, provider_ready: bool) -> tuple[ReadinessStep, ...]:
    states = ["ready", "ready"]
    states.append("ready" if viewer else "current")
    states.append("ready" if viewer and viewer.membership else "current" if viewer else "pending")
    states.append("ready" if viewer and viewer.membership and provider_ready else "current" if viewer and viewer.membership else "pending")
    return tuple(ReadinessStep(label, state) for label, state in zip(("本机安装", "中央服务", "飞书登录", "团队", "业务能力"), states, strict=True))


def _readiness_strip(steps: tuple[ReadinessStep, ...]) -> str:
    items = []
    for step in steps:
        symbol = "✓" if step.state == "ready" else ""
        items.append(f'<li class="{step.state}"><span class="step-node" aria-hidden="true">{symbol}</span><span>{escape(step.label)}</span></li>')
    return '<ol class="readiness" aria-label="能力就绪状态">' + "".join(items) + "</ol>"


def _account_blocker(viewer: ViewerState, provider: ProviderStatus) -> str:
    if viewer.membership is None:
        heading, message, action, href = "还没有加入 QA 团队", "加入现有团队或创建新团队后即可继续。", "加入或创建团队", "/start"
    elif not provider.reachable:
        heading, message, action, href = "飞书身份服务不可达", "请先启动本机 Feishu OAuth 服务。", "重新检查", "/account"
    else:
        heading, message = "Feishu Provider 尚未配置完整", "平台管理员需要完成应用与准入租户配置。"
        action = "查看平台状态" if viewer.is_platform_admin else "稍后重试"
        href = "/platform?section=feishu" if viewer.is_platform_admin else "/account"
    return f'<section class="status-panel warning"><span class="status-mark" aria-hidden="true">!</span><div><h2>{escape(heading)}</h2><p>{escape(message)}</p></div><a class="button primary" href="{href}">{escape(action)}</a></section>'


def _definition_rows(rows: tuple[tuple[str, str], ...]) -> str:
    return '<dl class="definition-list">' + "".join(f"<div><dt>{escape(label)}</dt><dd>{value}</dd></div>" for label, value in rows) + "</dl>"


def _member_row(member: TeamMemberRecord, csrf_token: str) -> str:
    user = member.user
    membership = member.membership
    other_role = "member" if membership.role == "team_admin" else "team_admin"
    return f"""<tr><td><div class="member-identity"><span class="member-avatar" aria-hidden="true">{escape(_initials(user.display_name))}</span><span><strong>{escape(user.display_name)}</strong><small>{escape(user.verified_name or '飞书用户')} · <code>{escape(user.user_ref)}</code></small></span></div></td><td>{escape(membership.team_remark or '—')}</td><td>{escape(_role_label(membership.role))}</td><td>{escape(_date(membership.joined_at))}</td><td><span class="inline-status ready">正常</span></td><td><form class="role-form" method="post" action="/team/members/{escape(user.user_ref, quote=True)}/role">{_csrf(csrf_token)}<input type="hidden" name="role" value="{other_role}"><button class="text-action" type="submit">{'降为成员' if other_role == 'member' else '设为管理员'}</button></form></td></tr>"""


def _invite_row(invite: InviteRecord, csrf_token: str) -> str:
    return f"""<div class="invite-row"><div><strong>剩余 {invite.remaining_uses} 次 · {_date(invite.expires_at)} 到期</strong><small>{escape(invite.issuer_display_name)} 签发 · <code>{escape(invite.invite_ref)}</code></small></div><form method="post" action="/team/invites/{escape(invite.invite_ref, quote=True)}/revoke">{_csrf(csrf_token)}<button class="text-action danger-text" type="submit">撤销</button></form></div>"""


def _invite_drawer(viewer: ViewerState) -> str:
    assert viewer.team is not None
    return f"""<a class="drawer-scrim" href="/team" aria-label="关闭邀请抽屉"></a><aside class="drawer" aria-labelledby="invite-title"><header><div><p class="context-label">团队邀请</p><h2 id="invite-title">生成邀请链接</h2></div><a class="drawer-close" href="/team" aria-label="关闭">×</a></header><form class="drawer-form" method="post" action="/team/invites"><div class="drawer-body"><div class="read-only-field"><span>目标团队</span><strong>{escape(viewer.team.name)}</strong></div><label class="field"><span>有效期</span><select name="expires_in"><option value="604800">7 天</option><option value="86400">24 小时</option><option value="2592000">30 天</option></select></label><label class="field"><span>可使用次数</span><select name="max_uses"><option value="5">5 次</option><option value="1">1 次</option><option value="10">10 次</option></select></label><p class="field-help">邀请只能由当前飞书租户内的用户使用；完整链接仅显示一次。</p>{_csrf(viewer.csrf_token)}</div><footer><button class="button primary wide" type="submit">创建邀请</button></footer></form></aside>"""


def _platform_section(section: str, viewer: ViewerState, provider: ProviderStatus, admins: tuple[PlatformAdminRecord, ...], counts: dict[str, int]) -> str:
    if section == "feishu":
        status = "已配置" if provider.reachable and provider.configured else "需要处理"
        app_id = f"<code>{escape(provider.app_id)}</code>" if provider.app_id else "—"
        tenant = f"<code>{escape(provider.tenant_ref)}</code>" if provider.tenant_ref else "—"
        callback = f"<code>{escape(provider.callback_uri)}</code>" if provider.callback_uri else "—"
        return f"""<header class="settings-heading"><div><p class="context-label">Provider / Feishu</p><h2>飞书读写与身份能力</h2><p>Provider 独立拥有应用密钥、OAuth 与 Token；平台只读取安全状态。</p></div><span class="status-badge">{status}</span></header>{_definition_rows((('服务连接', '可达' if provider.reachable else '不可达'), ('App ID', app_id), ('准入租户', tenant), ('已授权 Profile', str(provider.authorized_profiles)), ('OAuth 回调', callback)))}<div class="settings-actions"><code>uv run --project providers/feishu/auth-service feishu-auth configure</code><p class="security-note">配置会话短时运行，App Secret 不进入本页面。</p></div>"""
    if section == "admins":
        rows = "".join(_platform_admin_row(admin, viewer) for admin in admins)
        return f"""<header class="settings-heading"><div><p class="context-label">访问控制</p><h2>平台管理员</h2><p>管理员维护中央服务，不因此获得团队业务内容。</p></div></header><form class="admin-add-form" method="post" action="/platform/admins">{_csrf(viewer.csrf_token)}<label class="field"><span>用户标识</span><input name="user_ref" required placeholder="user_..."></label><button class="button primary" type="submit">添加平台管理员</button></form><div class="admin-list">{rows}</div><p class="settings-note">系统始终保留至少一名有效平台管理员。</p>"""
    if section == "reset":
        return f"""<header class="settings-heading"><div><p class="context-label danger-text">开发工具</p><h2>重置开发状态</h2><p>仅用于当前开发阶段，不是退出登录或管理员交接。</p></div></header><section class="danger-zone"><h3>删除当前实例的全部平台测试状态</h3><p>删除平台身份、团队、会话、邀请和审计，并返回一次性 Bootstrap 起点。Feishu Provider 凭证与飞书远端文档不会被删除。</p><form method="post" action="/platform/reset">{_csrf(viewer.csrf_token)}<label class="field"><span>输入 RESET 确认</span><input name="confirmation" autocomplete="off" required></label><button class="button danger" type="submit">重置平台状态</button></form></section>"""
    status = "运行正常" if provider.reachable else "Provider 不可达"
    return f"""<header class="settings-heading"><div><p class="context-label">中央控制面</p><h2>服务概览</h2><p>数据来自当前 SQLite 实例和 Provider 实时状态。</p></div><span class="status-badge">{status}</span></header><div class="overview-highlight"><strong>中央服务正常</strong><span>{counts['users']} 位用户 · {counts['teams']} 个团队 · {counts['platform_admins']} 位平台管理员</span></div>{_definition_rows((('当前用户', f'<code>{escape(viewer.user.user_ref)}</code>'), ('平台角色', '平台管理员'), ('持久状态', 'SQLite schema v1 · 开发模式'), ('Feishu Provider', '可达' if provider.reachable else '不可达')))}"""


def _platform_admin_row(admin: PlatformAdminRecord, viewer: ViewerState) -> str:
    remove = f"<form method='post' action='/platform/admins/{escape(admin.user.user_ref, quote=True)}/revoke'>{_csrf(viewer.csrf_token)}<button class='text-action danger-text' type='submit'>移除</button></form>"
    return f"<div><span class='member-avatar'>{escape(_initials(admin.user.display_name))}</span><span><strong>{escape(admin.user.display_name)}</strong><small>{escape(admin.user.verified_name or '飞书用户')} · <code>{escape(admin.user.user_ref)}</code></small></span><span class='inline-status ready'>有效</span>{remove}</div>"


def _csrf(token: str) -> str:
    return f'<input type="hidden" name="csrf" value="{escape(token, quote=True)}">'


def _public_csrf(token: str) -> str:
    return f'<input type="hidden" name="public_csrf" value="{escape(token, quote=True)}">'


def _notice(message: str | None) -> str:
    return f'<div class="form-notice" role="alert">{escape(message or "")}</div>'


def _role_label(role: str) -> str:
    return "团队管理员" if role == "team_admin" else "普通成员"


def _initials(value: str) -> str:
    normalized = value.strip()
    return (normalized[:2] or "QA").upper()


def _date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _current(is_current: bool) -> str:
    return ' class="current" aria-current="page"' if is_current else ""
