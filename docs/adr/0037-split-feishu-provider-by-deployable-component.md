---
status: accepted
date: 2026-08-25
---

# Feishu Provider 按独立部署单元拆分

Feishu Provider 继续拥有一个完整的外部系统领域边界，但源码在 `providers/feishu/` 内拆分为 `auth-service`、`mcp-server` 与 `protocol` 三个独立项目。授权控制面拥有 OAuth、Profile、租约和管理入口；MCP Server 拥有飞书资源操作与租约客户端；`protocol` 只保存两者共享的飞书私有通信模型，不包含任一侧实现。

拒绝继续用一个 Python 项目打包授权服务与 MCP，因为这会让执行客户端直接导入控制面实现，并迫使 `workspace-feishu` Plugin 携带不负责启动的完整授权服务。Plugin runtime 只打包 MCP Server、Feishu 私有协议与公共能力契约；本机开发模式仍可连接独立运行的 localhost 授权控制面，迁移不得改变现有工具 Schema、授权状态或读取语义。
