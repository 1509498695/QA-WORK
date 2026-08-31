# Feishu Provider

Feishu Provider 是 QA Skill Hub 中独立于 `lg-feishu` 的公共能力。它把长期授权管理与资源读取/受控单 Sheet 写入分成两个部署单元，并通过私有协议交换最长十分钟的任务租约。

首次交付支持三种受控放置模式：接管用户指定的内容空白 Sheet、在现有工作簿中新建 Sheet，以及在精确 Wiki 父节点下新建一个工作簿后接管其唯一默认 Sheet。第三种模式在零写预览中完整分页固化父节点直接子项，使用独立的 Wiki 读取/列举/创建 Scope，并在文件创建后先保存 `workbook_created` 检查点，再进入既有单 Sheet 写入与双重验证链。

## 组件

| 组件 | Python distribution | 职责 |
|---|---|---|
| `protocol` | `workspace-feishu-protocol` | capability ID、租约请求/交付和执行客户端身份模型 |
| `auth-service` | `workspace-feishu-auth-service` | 部署绑定、OAuth、Profile、Scope 映射和租约签发 |
| `mcp-server` | `workspace-feishu-mcp-server` | locator、Docx/Wiki/Sheets 读取、受管单 Sheet 写入、恢复和双重验证 |

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

受管写入的本地操作状态位于同一命名空间的 `operations-v1.sqlite3`。文件名保持兼容，内部 Schema v3 以加法迁移保留既有首次交付登记，并增加受管版本历史、单活动修订锁与修订检查点。数据库只保存摘要、证据哈希、检查点和 DPAPI 加密目标，不保存交付单元格正文；完整正文必须由调用方在应用或修订阶段重新提交。

## 受管工作表修订（v0.7）

首次交付成功结果会返回稳定 `registration_ref` 与 `managed_version=1`。调用方后续仍持有上一版完整规范时，可以先用 `feishu_managed_sheet_registration_resolve` 把用户提供的精确 Sheet/Wiki 链接唯一映射到登记，再单次调用 `feishu_managed_sheet_revise`，传入登记引用、稳定 `task_ref`、完整 `base_spec` 与 `next_spec`。

修订在任何远端写入前完成 API 与临时 XLSX 双重基线验证；名称和排序变化只刷新展示信息，隐藏、删除、替换、Profile 不匹配或语义漂移均保持零写入。哈希相同返回 `no_change`，不确认、不写入、不增版本。有变化时只显示一次有界 MCP 确认，然后按 `revision_reserved` 到 `version_committed` 的固定检查点序列前向执行。

缩表不会删除网格行列：Provider 清空 `base_rect - next_rect` 的业务内容和样式，并把完全退役的行、列分别重置为 `24 px`、`100 px`。最终状态只有在下一版完整矩形、退役区清理、API 回读与 XLSX 样式/尺寸验证全部完成后才提交新版本。相同规范对与 `task_ref` 的重试复用原操作，不再次确认、不换目标、不自动回滚。

本纵切仍是同任务能力：Provider 不保存规范正文，也不从远端表格反推 `base_spec`。跨任务的调用方基线资产持久化属于业务 Skill；Provider 的 `delivered` 与调用方的 `revision_ready` 必须分别报告。
