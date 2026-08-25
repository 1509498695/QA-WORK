---
status: accepted
date: 2026-08-19
---

# 使用一个飞书专用应用承载多个独立用户 Profile

新 Feishu Provider 创建并使用一个自身专用的飞书企业自建应用，同一准入企业租户内所有消费该 Provider 的业务 Skill 复用该应用，不按 Skill 重复创建飞书应用。该专用应用及其 App ID、App Secret、回调配置和应用级状态完全归 Feishu Provider 私有管理，不调用、复制或复用 `lg-feishu` 的应用、凭证、用户 Token、授权会话或资源 registry；业务 Skill 不能传入或选择另一套应用凭证改变执行边界。

每个 Provider Profile 通过该专用应用独立完成用户 OAuth，并分别保存用户主体绑定、`user_access_token`、`refresh_token`、实际授予 Scope 和有效期。多个 Profile 共享应用注册不代表共享用户身份、资源可见性或授权状态；一个 Profile 的新增 Scope、刷新失败、注销和撤销都不得影响或替代另一个 Profile。对用户来源和交付资源仍使用任务绑定的用户委托身份，专用应用的应用身份只用于 ADR 0005 允许的 Provider 自有内部资源。

## Considered Options

- 复用 `lg-feishu` 已有飞书应用：拒绝，因为应用凭证、回调、Scope、Token 和运维状态会继续受外部 Provider 控制，无法形成真正独立能力。
- 每个业务 Skill 创建一个飞书应用：拒绝，因为相同外部能力会产生重复审批、重复凭证、Scope 漂移和无法统一撤销的身份碎片。
- 所有用户共用一个用户 Token：拒绝，因为资源权限、审计主体、撤销和最小权限必须属于具体用户 Profile。
- 允许业务 Skill 临时传入 App ID 与 App Secret：拒绝，因为同一 Provider 操作会在未受治理的应用身份之间切换，并使凭证进入业务 payload。

## Consequences

- Feishu Provider 的安装就绪、专用应用配置就绪和具体 Profile OAuth 就绪是不同状态，必须分别报告。
- 更换 App ID 视为切换 Provider 专用应用，会使现有 Profile、Scope 证明和应用私有资源绑定失效；不得静默沿用。
- App Secret 轮换及用户 Token 刷新由 Provider 凭证层处理，仓库、任务目录、日志和 MCP 正文不得出现明文凭证。
- 专用应用凭证与用户长期授权由 ADR 0023 定义的飞书授权控制面托管；该决定仍不证明控制面已经部署或可用。
- 企业自建应用的首阶段租户范围遵循 ADR 0025；跨企业使用不能通过增加 Profile 绕过应用类型和租户准入。
