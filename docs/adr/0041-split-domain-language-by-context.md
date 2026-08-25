---
status: accepted
date: 2026-08-25
---

# 按公共能力、Feishu Provider 与业务 Skill 拆分领域语言

根 `CONTEXT-MAP.md` 只负责列出上下文及其关系；`platform/CONTEXT.md` 只定义 Provider 中立的能力、操作、证据和预检语言；`providers/feishu/CONTEXT.md` 定义租户、Profile、OAuth、租约、执行客户端与飞书资源；`skills/qa-case-xlsx-local/CONTEXT.md` 继续定义来源、用例、审计和本地交付。授权控制面与 MCP Server 属于同一个 Feishu Provider 上下文，共享一份领域词汇表。

目录路径、Python 包、部署拓扑、版本、当前实现和未来计划不是领域术语，必须保留在 README、ADR 或实施计划中，不再混入 `CONTEXT.md`。这个边界避免公共能力语言被 Feishu 实现细节占据，也避免为每个部署组件建立重复词汇表。
