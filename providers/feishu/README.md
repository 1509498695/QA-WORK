# Feishu Provider

Feishu Provider 是 QA Skill Hub 中独立于 `lg-feishu` 的只读公共能力。它把长期授权管理与资源读取分成两个部署单元，并通过私有协议交换最长十分钟的任务租约。

## 组件

| 组件 | Python distribution | 职责 |
|---|---|---|
| `protocol` | `workspace-feishu-protocol` | capability ID、租约请求/交付和执行客户端身份模型 |
| `auth-service` | `workspace-feishu-auth-service` | 部署绑定、OAuth、Profile、Scope 映射和租约签发 |
| `mcp-server` | `workspace-feishu-mcp-server` | locator、Docx/Wiki/Sheets 只读操作和租约客户端 |

依赖方向固定为：Auth Service 与 MCP Server 都可以依赖 `protocol` 和公共 `capability_contracts`；`protocol` 不依赖两侧实现；MCP Server 不导入 Auth Service。跨组件行为由 `tests/integration/` 验证。

## 开发命令

从仓库根运行：

```powershell
uv sync
uv run --package workspace-feishu-auth-service pytest providers/feishu/auth-service/tests
uv run --package workspace-feishu-mcp-server pytest providers/feishu/mcp-server/tests
uv run pytest providers/feishu/tests/integration
```

本机服务入口继续使用根脚本：

```powershell
.\scripts\configure-local-auth.ps1
.\scripts\run-local-auth.ps1
.\scripts\run-feishu-provider.ps1
```

部署绑定和 Profile 仍保存在版本库外的既有 `%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\` 命名空间。目录重构不会移动、删除或重新加密这些文件。
