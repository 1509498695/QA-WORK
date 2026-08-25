---
status: accepted
date: 2026-08-25
---

# 使用虚拟 uv Workspace 组织 Python 组件

仓库根 `pyproject.toml` 只定义不发布的开发 Workspace，根 `uv.lock` 统一解析 `platform/capability-contracts`、`providers/feishu/protocol`、`providers/feishu/auth-service` 与 `providers/feishu/mcp-server`，以支持同一提交中的合同修改和跨组件测试。每个成员仍拥有独立包名、语义版本、构建配置和测试，根目录不再发布当前的 `workspace-feishu-auth` 聚合包。

共享开发锁不构成发行耦合：Plugin runtime、授权控制面部署物及未来其他 Provider 分别生成自己的发行锁与构建证据。`skills/qa-case-xlsx-local` 使用 Codex bundled runtime，不加入 Python Workspace；根 `tests/` 仅保留真正跨组件的合同和集成测试。
