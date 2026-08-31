# Workspace Feishu 首个受管单 Sheet 写入纵切

## 状态

v0.5.0 的设计、实现、自动化验证、Plugin/Skill 校验、私有 runtime 重建、个人 Marketplace 更新和真实对象写入验证均已完成。纵切只面向 `local-dev-v0` 的 Windows 当前用户，写入用户明确指定的 Sheets/Wiki 工作簿中的一个工作表；不开放任意 OpenAPI、Docx 写入或非空 Sheet 覆盖。真实验证证明了首次接管内容空白工作表和在既有工作簿中新建工作表两条交付路径；它不证明已支持同一受管工作表的后续修订、正式多人部署或任意既有非空工作表覆盖。

## 用户可见目标

- 用户提供一个内容空白工作表链接时，按 URL 的 `?sheet=<sheet_id>` 精确接管；URL 无选择器时，仅在工作簿恰好有一个普通可见工作表时接管。
- 用户提供现有工作簿链接并要求新增 Sheet 时，必须给出明确且唯一的 Sheet 名称；即使 URL 带 Sheet 选择器，也只定位工作簿，不修改被选择的旧 Sheet。
- 一次交付只产生一个受管工作表。工作簿及其他工作表继续由用户拥有。

## 公开合同

### 交付规范

`workspace-feishu/sheet-delivery/v1` 完整声明：

- 零基、半开交付矩形的行列数量；
- 每个单元格的标量值或显式公式；
- 基础样式和不重叠样式覆盖；
- 默认/覆盖行高、列宽；
- 冻结行列和不重叠合并区域。

矩形内部是完整状态，而不是增量补丁。Provider 先清理矩形内旧样式，再写入全部声明状态。矩形外必须无业务内容；接管模式保留矩形外空白单元格已有样式，新建模式保持默认状态。行高/列宽按整行/整列轴生效，因此矩形覆盖的前 N 行和前 M 列属于受管尺寸范围，而之后的行列尺寸保持不变。合并不能跨越矩形，且只有左上角可以含值。

当前公开样式接口没有可设置并精确验证的自动换行字段，文档只明确证明 `FULL_BORDER`。首个纵切因此拒绝 `wrap_text=true` 和无证据的局部边框，只支持无边框或完整边框；不允许静默丢失样式。

### 两个语义工具

1. `feishu_managed_sheet_preview`：获取任务/Profile 租约，解析目标，完整检查空白接管前置条件或新建名称唯一性，规范化并摘要交付规范，在本地状态库建立十分钟不可变预览。它不产生飞书远端副作用。
2. `feishu_managed_sheet_apply`：重新接收并哈希同一规范，绑定预览、任务和目标；在首个远端副作用前通过 MCP elicitation 展示工作簿、工作表、动作、交付矩形、内容摘要和风险。只有客户端返回 `accept` 动作时才消费一次服务端授权；表单没有可由调用方替代接受动作的业务确认字段。

工具参数中没有 `confirmed=true`、聊天确认文本、原子 API body 或任意文件路径。拒绝、取消、确认能力缺失、预览过期和规范漂移均保持零远端写入。

## 远端步骤与检查点

接受后使用稳定工作表 ID 串行执行：

1. `target_registered`：重新检查前置状态；接管绑定既有 Sheet ID，新建校验无同名后创建并解析返回 ID。
2. `grid_extended`：仅补足交付矩形所需网格。
3. `values_written`：一次写入完整矩形值和公式。
4. `styles_cleared`：清理完整交付矩形样式。
5. `base_style_written`：写入完整基础样式。
6. `style_ranges_written`：先清理每个覆盖范围，再写入其完整解析样式。
7. `dimensions_written`：写默认行高/列宽，再写覆盖跨度。
8. `freeze_written`：写工作表级冻结行列。
9. `merges_written`：串行写合并区域。
10. `api_verified`：按 Formula 模式读取完整网格，验证 ID/标题、值、公式、合并、冻结和矩形外无内容。
11. `export_verified`：使用同一 Profile 导出整个工作簿到内存 XLSX，只映射目标标题，验证值/公式、样式、尺寸、冻结、合并和矩形外内容；压缩包、大小、条目和解压比均受限，不写入磁盘。

只有两个回读都成功，状态才提升为 `delivered` 并登记受管 Sheet。

## 本地状态与恢复

- SQLite 固定路径：`%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\operations-v1.sqlite3`。
- 目标工作簿/工作表元数据使用 Windows 当前用户 DPAPI 加密；数据库只持久化任务、Profile、模式、摘要、检查点、远端修订和安全诊断码，不保存单元格正文。
- 每次正式确认只产生并原子消费一次本地授权。恢复属于原授权操作，不再次弹确认，也不能更换任务、目标或规范。
- 单个远端请求超时、5xx 或成功响应合同不明时标记 `ambiguous`，不得盲目重发。下次只做完整 API+XLSX 对账；若不能证明最终状态，保留 `recovery_required` 并停止修改。
- 明确失败且不存在结果不明时可以从上一个已完成检查点前向继续。任何自动删除新建 Sheet、清空接管 Sheet、回滚样式或换目标都被禁止。
- API 已证明但导出失败时返回 `verification_incomplete`，后续只重跑安全回读/导出，不能提前声称交付完成。

## 安全上限

- 交付最多 5,000 行、100 列、200,000 个单元格、500 个结构范围、单元格文字 40,000 字符、规范化 JSON 8 MiB。
- 空白接管和最终 API 验证必须完整读取目标网格，最多 200,000 个网格单元格。
- XLSX 最大 25 MiB、10,000 个 ZIP 条目、200 MiB 解压总量、1,000 倍压缩比、目标工作表最多一百万个物理网格单元格。
- 所有飞书写请求严格串行；同一步包含多个请求时，前序成功而后序失败一律视为结果不明。

## 发布与验证门槛

- Auth Service 默认 OAuth 增加 `sheets:spreadsheet`、typed-cell 布尔值写入所需的 `sheets:spreadsheet:write_only` 与 `drive:export:readonly`，旧 Profile 必须重新授权；读、常规写、typed-cell 写和导出分别映射为独立语义能力。
- Plugin v0.5 声明 `Read`、`Write`、`Interactive`，新增独立 `feishu-write` Skill；只读与授权 Skill 不代替写入路由。
- 私有 runtime 必须由规范源确定性重建，包含 `openpyxl`，六个 MCP 工具均带输入/输出 Schema 和正确注解。
- 自动化覆盖规范拒绝、状态库加密与一次授权、确认拒绝零写、结果不明只读恢复、精确 Sheet 选择、内容空白判定、原子请求体、API 回读、XLSX 样式/尺寸/合并验证和不安全压缩包拒绝。
- 自动化和模拟响应不等于真实飞书交付。没有用户给出的真实目标和本次确认时，不执行远端写入，也不把功能实现标记为真实对象已交付。

## 已取得的验证证据

- 根 uv Workspace：117 项自动化测试通过。
- 独立本地业务 Skill：18 项测试通过。
- Python 编译、依赖锁、Provider 边界扫描、Git 空白检查通过。
- `workspace-feishu` Plugin 清单及 `feishu-read`、`feishu-write`、`feishu-auth` Skills 通过本地校验器。
- 私有 runtime 与规范源逐字节一致，锁文件包含 `openpyxl 3.1.5`；隔离环境启动后公开六个带输出 Schema 的 MCP 工具。
- 个人 Marketplace 已安装 `workspace-feishu 0.5.0` 的当前缓存版本；每次更新后仍需由新任务加载对应 `feishu-write` Skill 和两项写入工具。
- 截至 2026-08-27，本机操作状态库记录了 3 次真实 `delivered`：1 次 `adopt_blank_sheet` 在远端修订 `14` 完成，2 次 `create_new_sheet` 分别在远端修订 `41`、`57` 完成；三次均到达 `export_verified`，并持久化了 API 与 XLSX 双重回读组成的交付证据哈希。
