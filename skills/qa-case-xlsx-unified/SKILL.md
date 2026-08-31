---
name: qa-case-xlsx-unified
description: 读取用户明确提供的飞书 Docx、Wiki 或 Sheets 策划来源，生成可追溯的陈镇个人风格 A:J 测试用例，并受控写入用户指定的飞书工作簿，或在指定 Wiki 父节点下新建表格文件后写入。飞书来源生成、在线用例交付或同对象回读时使用；纯本地来源与本地 Excel 交付继续使用 qa-case-xlsx-local。
---

# qa-case-xlsx-unified

把完整飞书策划来源转换成可复核的 A:J 测试用例，并通过 Workspace Feishu Provider 交付到用户明确指定的工作簿。当前纵切只实现飞书来源与飞书 Sheets 交付；不得把它表述为本地/飞书混合来源已经完成。

## 固定边界

- 飞书读取和写入只使用已安装 `workspace_feishu` MCP 的公开工具；不调用 `lg-feishu`，不导入 Provider 源码、运行时、凭据或本机状态。
- 不修改 `qa-case-xlsx-local` 的纯本地合同。纯本地来源和本地 `.xlsx` 交付由该 Skill 独立处理，不静默转发。
- 只读取用户本次明确提供的飞书定位符，不跟随文档中的外链，不扫描历史任务猜测来源。
- 只写用户本次明确指定的 Sheets/Wiki 工作簿；用户明确选择 `create_new_workbook` 时，只在其指定的 Wiki 父节点下创建一个工作簿。来源链接、历史交付链接或模型推断不能自动成为写入目标。
- 当前正式交付一次只管理一个 Sheet；不删除、清空、改名或覆盖其他 Sheet。
- 正式交付必须经过 Provider 的一次 MCP 确认卡片。聊天中的“确认”不能替代卡片，Skill 不得代替用户接受。

## 运行前

1. 完整读取本文件。
2. 读取 [unified-source-v2.md](references/unified-source-v2.md)、[generation-contract.md](references/generation-contract.md) 和 [feishu-delivery.md](references/feishu-delivery.md)。
3. 完整读取已安装 `workspace-feishu:feishu-read` 与 `workspace-feishu:feishu-write` Skill；授权问题按 `workspace-feishu:feishu-auth` 处理。
4. 读取 `qa-case-xlsx-local` 的 `generation-blueprint.md`、`humanization.md`、项目路由和发布规则索引，只复用其公开生成规则与校验 CLI；不得调用其本地来源提取或本地工作簿构建阶段处理飞书内容。
5. 调用工作区依赖加载器，使用返回的 Python 运行本 Skill 的脚本和生成门禁。

## 最小输入

- 一个或多个明确的飞书 Docx、Wiki 或 Sheets 来源链接。
- 一个明确的飞书 Sheets/Wiki 目标工作簿链接；或一个明确的不带 `sheet=` 的 Wiki 父节点链接。
- 在现有工作簿新建 Sheet 时提供明确唯一的 Sheet 标题；在 Wiki 父节点下新建文件时提供明确唯一的工作簿文件名。未给出时可建议 `<需求名>-测试用例-<YYYYMMDD>`，但必须在预览前让用户看到实际名称。

来源与目标可以位于同一工作簿，但必须保持不同 Sheet。若目标 URL 带 `sheet=` 且用户要求新建 Sheet，该选择器只定位工作簿，不能覆盖被选中的 Sheet。

## 标准流程

### 1. 固化飞书来源

- 为本次运行生成不含业务内容的稳定 `task_ref`，并按统一来源 v2 契约保存来源回执、规范化快照和内容哈希。
- Wiki 类型不明时按 `workspace-feishu:feishu-read` 路由；读取结果必须保留真实对象类型、修订、范围、资产引用和完整性警告。
- `status=ok` 且来源级 `retrieval_complete=true` 才可进入正式生成。授权缺失、类型不明、任一来源读取不完整或视觉资产未复核时停止在来源门禁，不预览远端写入。

### 2. 生成并校验用例

- 严格区分来源事实、生成蓝图、基础用例、规则增量、最终用例和待确认边界。
- 主来源是本次飞书策划案；历史用例只用于风险发现，不能覆盖当前来源事实。
- 使用自然用例数量，不隐藏配额。最终固定 A:J：`用例编号、一级模块、二级模块、检查点、前置条件、操作步骤、预期结果、优先级、测试结果、备注`。
- 生成完整审计产物并运行 `qa-case-xlsx-local` 已公开的 `validate-rules` 与 `validate-run` 门禁。只调用其 CLI，不跨目录导入 Python 模块。
- `pending_boundary_confirmations` 非空、生成门禁失败或最终用例为空时，不构建飞书写入预览。

### 3. 构造不可变 Sheet 规范

运行：

```powershell
& <bundled-python> <skill-root>\scripts\build_feishu_case_spec.py build `
  --final-cases <task-root>\audit\final_cases.json `
  --title <需求名-测试用例> `
  --out <task-root>\delivery\sheet-spec.json
```

然后执行 `verify` 子命令对 `final_cases.json` 与规范做确定性回读。脚本只构造 JSON，不访问网络，也不产生本地 `.xlsx`。

### 4. 预览并交付

- 目标是现有工作簿时默认使用 `create_new_sheet`，避免覆盖来源或现有业务 Sheet；只有用户明确提供内容空白 Sheet 并要求接管时才使用 `adopt_blank_sheet`。目标是 Wiki 父节点且用户要求在目录下新建表格文件时，使用 `create_new_workbook`，提交 `requested_workbook_title` 并省略 `requested_sheet_title`。
- 使用稳定 `task_ref` 调用 `feishu_managed_sheet_preview`，再把同一 `operation_ref`、同一 `task_ref` 和逐字节等价的完整规范交给 `feishu_managed_sheet_apply`。
- Provider 卡片是唯一写入确认；不得在其前后增加业务上没有必要的第二个确认。
- 已登记 Sheet 的后续修订只在当前调用方持有完整上一版规范时使用 `feishu_managed_sheet_registration_resolve` 与 `feishu_managed_sheet_revise`。

### 5. 同对象回读

- 只有 Provider 返回 `status=delivered`、`last_completed_step=export_verified` 且 `evidence.retrieval_complete=true`，才进入最终回读。
- 使用返回的新工作簿 token/Wiki 节点身份和精确 Sheet ID 再次只读，逐格核对 A:J 标题、表头、用例数和值，并确认工作簿 token、Wiki 节点、Sheet ID 与远端 revision 没有切换。
- 把交付结果与回读哈希写入本次审计目录；只有同对象语义回读一致才报告“飞书用例已交付”。

## 失败与恢复

- `auth_required`：停止并交给 `workspace-feishu:feishu-auth`；授权后重做原读取或零写入预览。
- `retrieval_incomplete`：保留已读证据和警告，不生成正式交付，也不忽略该来源。
- `declined` / `cancelled`：停止，零远端写入。
- `recovery_required` / `verification_incomplete`：保留原操作、任务、父节点/工作簿目标和完整规范，只允许同操作对账或安全前向恢复；不重新预览、不换 Sheet/目录、不新建替代用例表或替代工作簿。
- `preview_expired`、目标漂移或规范改变：重新构建规范和预览，并重新取得 MCP 卡片确认。
- 任何测试通过、文件存在或 Provider 部分进度都不能替代真实同对象回读。

## 当前纵切完成定义

- 新 Skill 结构、确定性规范脚本和离线契约测试通过，且 `qa-case-xlsx-local` 全部回归仍通过。
- 一个真实飞书来源取得完整证据并生成通过门禁的 A:J 用例。
- 在用户指定工作簿中新建一个唯一命名 Sheet；来源 Sheet 和其他 Sheet 保持不变。
- 写入结果为 `delivered`、`retrieval_complete=true`，并完成同 Sheet ID 的逐格语义回读。
- 未把尚未实现的混合来源、跨任务基线自动找回或正式多人部署表述为完成。
