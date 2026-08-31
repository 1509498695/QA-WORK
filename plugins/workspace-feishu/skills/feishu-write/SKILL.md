---
name: feishu-write
description: 使用独立 Workspace Feishu Provider，把版本化的结构化单 Sheet 规范受控写入或修订用户指定的飞书 Sheets/Wiki 工作簿，或在用户指定的 Wiki 父节点下新建工作簿后写入。用户要求向指定飞书表格链接写入、接管内容空白工作表、在现有在线工作簿中新建 Sheet、在 Wiki 目录下新建表格文件，或更新已登记的受管 Sheet 时使用；只读分析、OAuth 管理、Docx 写入、本地文件写入不触发。
---

# Workspace Feishu Managed Sheet Write

## 固定边界

- 首次交付只调用本插件 `workspace_feishu` MCP 的 `feishu_managed_sheet_preview` 与 `feishu_managed_sheet_apply`；既有受管工作表修订使用 `feishu_managed_sheet_registration_resolve` 与单次 `feishu_managed_sheet_revise`。
- 不调用、转发或复用 `lg-feishu` 的 MCP、Skill、凭据、Profile、缓存或运行时。
- 只写用户本次明确指定的 Sheets URL、指向 Sheet 的 Wiki URL，或在 `create_new_workbook` 时使用用户明确指定的 Wiki 父节点 URL；不得把读取来源、历史链接或模型猜测当成写入目标。
- 一次交付只管理一个工作表。工作簿仍归用户所有；不得删除、清空、改名或修改其他工作表。
- `profile_ref` 默认省略，由本机授权服务选择唯一 Profile；不要把个人 Profile 固化进 Skill。
- 不要求用户复制、保存或输入 `registration_ref`；用其提供的精确 Sheet/Wiki 链接只读解析。未指定 Sheet 时，只有工作簿恰好存在一条受管登记才允许自动选择。

## 交付规范

- `spec.schema_version` 固定为 `workspace-feishu/sheet-delivery/v1`。
- 规范必须完整声明矩形内的值/公式、基础样式、样式覆盖、行高、列宽、冻结和合并；文字以 `=` 开头时不得冒充公式，公式使用 `{ "formula": "=..." }`。
- 当前版本只支持可验证的无边框或完整边框，不支持自动换行、局部边框、图表、批注和嵌入媒体。收到 `unsupported_delivery_spec` 时停止，不得丢字段后重试。
- 正式应用必须原样重传预览时的完整 `spec`；不得根据预览结果重新排序、补默认值或修改内容。

## 放置模式

### 接管内容空白工作表

使用 `placement_mode=adopt_blank_sheet`：

1. 优先要求 URL 带精确 `?sheet=<sheet_id>`；若没有选择器，工作簿必须恰好只有一个普通可见工作表。
2. `requested_sheet_title` 必须省略；接管不改名。
3. Provider 会完整读取目标网格，确认没有值、公式、富文本、链接、提及或合并。已有空单元格格式、尺寸和冻结不代表有内容。
4. 这是本机开发纵切中的非原子接管；预览和确认中必须保留风险披露。

### 在现有工作簿中新建 Sheet

使用 `placement_mode=create_new_sheet`：

1. 必须提交明确、唯一且合法的 `requested_sheet_title`。
2. URL 中即使带 `?sheet=`，也只用它定位工作簿，不把该 Sheet 当成写入目标；预览会明确报告选择器被忽略。
3. 同名冲突必须停止，不能自动改名、覆盖或切换成接管模式。

### 在 Wiki 父节点下新建工作簿

使用 `placement_mode=create_new_workbook`：

1. `locator` 必须是用户明确指定、且不带 `sheet=` 的精确 Wiki 父节点 URL；父节点本身可以是 Docx 等 origin 节点，它只定位新文件所在目录，不作为写入对象。
2. 必须提交明确、合法的 `requested_workbook_title`；`requested_sheet_title` 必须省略。Provider 保持飞书自动创建的唯一默认工作表原名，创建后再读取并固定其精确 Sheet ID 和标题。
3. 预览会完整分页读取父节点的直接子节点并做大小写不敏感的标题唯一性检查。标题冲突、父节点身份/标题变化或子节点集合漂移都停止；不得自动改名、换目录或改为现有工作簿模式。
4. 正式确认覆盖“创建一个新电子表格文件 + 写入其唯一默认工作表”。创建成功后立即保存 `workbook_created` 检查点，再固定默认 Sheet；任何结果不明都只能沿用同一操作对账或前向恢复。
5. 成功结果同时返回新工作簿 token、Wiki 节点 URL/Token、精确 Sheet ID/标题和登记引用。后续业务回读必须使用这些返回身份读取新对象，不能回读父节点或同名对象。

## 两阶段流程

1. 使用同一稳定且不含敏感信息的 `task_ref` 调用 `feishu_managed_sheet_preview`。
2. 仅当 `status=preview_ready` 时，向用户说明目标工作簿、工作表、放置模式、交付矩形、内容摘要、过期时间和全部风险披露。
3. 调用 `feishu_managed_sheet_apply`，传入预览返回的 `operation_ref`、同一 `task_ref` 和完全相同的 `spec`。
4. 正式工具会通过 MCP 客户端显示确认卡片。聊天中的“确认”、工具参数布尔值或调用方自建授权都不能替代该卡片；Skill 不得替用户接受。

## 既有受管工作表修订

修订纵切只适用于当前调用方仍持有上一版完整 `base_spec` 的场景：

1. 先用用户提供的精确链接调用 `feishu_managed_sheet_registration_resolve`。只接受唯一登记；零条或多条都停止，不按名称、索引或最近使用记录猜测。
2. 调用方必须持有登记当前版本对应的完整 `base_spec`，并已生成、审阅完整 `next_spec`。Provider 不保存业务正文，也不从远端反推规范；缺少基线时把 `base_spec_required` 原样报告。
3. 使用新的稳定 `task_ref` 单次调用 `feishu_managed_sheet_revise`，传入解析得到的 `registration_ref`、`base_spec` 与 `next_spec`。不要先自行创建第二个远端预览或要求用户管理 `operation_ref`。
4. Provider 会在任何写入前对基线完成 API + 临时 XLSX 双重验证。`baseline_verification_incomplete`、基线摘要不匹配、目标隐藏/删除/替换或 Profile 不匹配时保持零写入。
5. 若两份规范哈希相同，返回 `no_change`：不弹确认、不写入、不增加版本。只有 `evidence.retrieval_complete=true` 才能报告“无变化且基线已验证”。
6. 有变化时 MCP 只弹一次有界差异确认，展示精确目标、版本 `N → N+1`、旧新矩形、变更计数、退役区域以及完整 `preview_sha256`，不展开单元格或公式正文。Skill 不得替用户接受。
7. 缩表时 Provider 清除上一版退役区域的内容和样式，把完全退役行/列重置为 `24 px` / `100 px`；不物理删除网格轴，不新建替代 Sheet，不改 Sheet ID。
8. 相同 `registration_ref + task_ref + base_spec + next_spec` 重试自动复用原修订操作。`recovery_required` 或 `verification_incomplete` 时原样重传同一规范对；不得再次确认、换目标、回滚或盲目创建新版本。
9. 飞书 `style.clean=true` 的成功响应不单独证明退役样式已中性化；Provider 还要显式写入受控中性样式，并以 XLSX 证明退役单元格无业务格式。若返回 `xlsx_verify_retired_style:*`，保持原操作和规范对，由 Provider 按原授权补齐中性化并重新双读，不得忽略诊断或创建新修订。

Provider 只报告远端 `delivered` / `no_change`。调用方成功把已交付 `next_spec` 保存为下一基线后，才可另报 `revision_ready=true`；若调用方基线保存失败，保持远端交付事实并另报 `baseline_persistence_incomplete`，不得让 Provider 结果冒充调用方基线已持久化。

## 状态判定

- `delivered`：只有 `evidence.retrieval_complete=true`，且 API 与临时 XLSX 导出均验证同一稳定工作表后，才能报告完成。
- `declined` / `cancelled`：停止，零远端写入；若用户后来改变主意，重新创建预览。
- `recovery_required`：保留现场，不删除新 Sheet/新工作簿、不清空接管 Sheet、不换目标。用相同 `operation_ref`、`task_ref`、`spec` 再次调用应用工具，只允许 Provider 按原授权做回读对账或安全前向恢复。
- `verification_incomplete`：远端可能已经完成，但证据不足；同样使用原操作恢复验证，不声称交付成功。
- `no_change`：API 与临时 XLSX 已证明当前受管版本与 `base_spec` 一致，且 `next_spec` 没有变化；不产生新版本。
- `base_spec_required`：调用方缺少完整上一版规范；停止，不能让 Provider 从在线表格猜测或重建业务规范。
- `baseline_verification_incomplete`：写前 API/XLSX 证据不完整；保持零写入，先按稳定诊断修复验证链。
- `auth_required`：停止写入，交给 `feishu-auth` Skill 返回安全授权入口；完成增量授权后重新预览。
- `preview_expired`、`precondition_failed`、`write_conflict`：不得绕过；根据当前目标重新预览。

## 禁止事项

- 不直接调用飞书原子 OpenAPI，不接受 raw API body、任意 XLSX 路径或本地文件路径作为正式写入合同。
- 不自动删除、回滚、清空或盲目重试结果不明的远端请求；新工作簿创建不确定时必须先完整列举同一父节点并与预览基线对账。
- 不把业务单元格正文、Token、Secret 或用户身份写入 `task_ref`、日志或恢复说明。
- 不把登记解析当成写入授权；不得仅凭 `registration_ref` 覆盖任意内容，也不得绕过修订工具的一次 MCP 确认。
- 未收到用户写入请求时，不因读取、总结或测试代码而执行真实飞书写入。
