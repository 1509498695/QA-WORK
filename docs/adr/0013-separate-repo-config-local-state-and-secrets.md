---
status: accepted
date: 2026-08-19
---

# 分离仓库配置、本机私有状态与凭证

版本库只保存 Provider 逻辑名、契约兼容范围、所需 Scope 和其他非敏感声明；账号绑定、真实资源标识、受管对象登记及恢复检查点保存在仓库外的私有状态中；所有凭证只进入该 Provider 明确批准的凭证存储，业务 Skill 只能引用逻辑 Provider Profile，不能读取、传递或输出凭证。

## 范围更新

ADR 0023 已取代本 ADR 对飞书长期凭证物理位置的原始决定：飞书 App Secret 与用户 Refresh Token 由飞书授权控制面集中托管，本机操作系统凭证库只保存执行客户端身份材料；短期 User Access Token 只驻留执行客户端内存。未来 SVN 等本机 Provider 仍默认使用 Credential Manager 或 DPAPI 当前用户保护，除非另有 ADR。

## Considered Options

- 将凭证保存在被忽略的 `.env` 或 Workspace 配置中：拒绝，因为忽略规则不能防止日志、备份、误提交或跨进程读取泄漏。
- 将账号和受管对象状态提交到仓库：拒绝，因为机器用户绑定和真实资源标识不属于共享源码，并可能泄漏内部资源。
- 由每个业务 Skill 分别保存 Token：拒绝，因为同一凭证会出现多个副本，刷新、撤销和审计无法集中处理。

## Consequences

- `workspace.yaml`、Plugin 清单、任务目录、审计包、日志和 MCP 结果都不得出现明文凭证；敏感字段必须在序列化前拒绝或脱敏。
- Provider 卸载、Profile 注销和 OAuth 撤销必须分别处理源码配置、本机状态和凭证，不能把删除普通配置当作凭证已撤销。
- 自动化测试只能使用模拟凭证库或隔离测试账号，不得读取开发者真实凭证。
