---
name: feishu-read
description: 使用独立 Workspace Feishu Provider 只读读取飞书 Docx、Wiki 或 Sheets，并检查同一对象的完整性证据。用户提供飞书文档、Wiki、电子表格链接，要求读取、总结、分析或验证内容时使用；飞书授权管理、飞书写入、本地文件读写不触发。
---

# Workspace Feishu Read

## 边界

- 只使用本插件的 `workspace_feishu` MCP 工具。
- 不调用、转发或复用 `lg-feishu` 的 MCP、Skill、凭据、Profile、缓存或运行时。
- 本 Skill 只读，不创建、修改或删除飞书资源，也不写入本地业务文件。
- `profile_ref` 默认省略；本机中央服务会自动选择唯一已授权 Profile。不要把个人 Profile 写死进 Skill。

## 路由

1. 收到 `/sheets/` 链接时调用 `feishu_sheets_read`。
2. 收到 `/docx/` 链接或 Docx token 时调用 `feishu_docx_read`。
3. 收到 `/wiki/` 链接时，依据用户明确说明选择 Docx 或 Sheets reader；若类型未说明，先调用 Sheets reader，且只在返回 `unsupported_resource` 且 `wiki_object_type=docx` 时改用 Docx reader。
4. `task_ref` 使用本次请求内稳定、无敏感信息的短标识；不要放入文档内容、账号、Token 或 Secret。

## 结果判定

- `status=ok`：可使用返回内容，并报告标题、修订版本、内容哈希与读取范围。
- `status=retrieval_incomplete`：可以报告已读取内容，但必须逐条呈现 `evidence.warnings`，不得声称完整回读。
- `status=auth_required`：停止内容读取，向用户返回 `details.authorization_url` 的可点击 Markdown 链接，链接文字使用“重新进行飞书授权”。用户确认授权成功后，重新执行原读取。
- `status=profile_selection_required`：仅当本机存在多个授权 Profile 时，向用户展示非敏感 `profile_refs` 并请求选择。
- 其他失败状态：报告安全的 `status` 与 `message`；不要猜测内容，也不要展示底层响应或凭据。

## Sheets 默认范围

- 未明确缩小范围时读取整个工作簿，包括全部工作表。
- 报告 `sheet_count`、`returned_sheet_count`、`requested_cell_count`、`returned_value_count` 与每个工作表的 `retrieval_complete`。
- 同一对象验证需要再次读取并比对 `spreadsheet_token`、修订版本、工作表集合与 `evidence.content_hash`；动态内容确有变化时说明差异，不伪造稳定性。
