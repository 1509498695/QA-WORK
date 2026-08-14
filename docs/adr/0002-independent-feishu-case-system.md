---
status: accepted
date: 2026-08-14
---

# 独立飞书用例系统

`qa-case-xlsx` 作为拥有自身 Workspace 身份、Git origin、任务证据和交付状态的独立系统运行。它可以依赖 Codex 基础运行时与 LG Feishu 插件完成 OAuth、飞书读取、受控写入和语义回读，但不得导入、调用或引用 QAWORK 的代码、命令、目录、模板绑定或私有状态；这项决定取代 ADR-0001 中“禁止飞书与网络依赖”的边界，同时保留独立生成核心和本地审计链。

## Considered Options

- 继续借用 QAWORK：拒绝，因为授权身份、运行状态和交付恢复仍会与 QAWORK 耦合。
- 自建飞书应用、OAuth 服务和 OpenAPI 客户端：拒绝，因为它会重复现有安全能力，并扩大凭证、幂等和恢复责任。

## Consequences

- 独立目录必须拥有有效的 `workspace.yaml`，且 `origin` 必须指向只属于 `qa-case-xlsx` 的专属 Git 仓库；不得复用、别名指向或回退到 QAWORK 的 remote。
- 专属 Git origin 是飞书 Provider 身份的稳定组成部分。origin 缺失、歧义或发生未确认变更时，所有飞书操作必须失败关闭，直到身份迁移或重新核验完成。
- 飞书读写必须使用 LG Feishu 的命名能力、完整性门禁、预览确认、幂等检查点和写后回读，不得用原始 OpenAPI 或本地网络脚本绕过。
- 在线读取或写入未完成时必须保留可恢复状态，不得宣称正式交付成功。
