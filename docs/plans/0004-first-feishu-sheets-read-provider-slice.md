# 首个 Feishu Sheets 与 Wiki→Sheets 只读 Provider 纵切

## 状态

v0.4.1 的代码合同、自动化验证、增量 OAuth、目标 Wiki Sheet 的真实双次回读和独立 Plugin 打包均已完成。

## 目标与边界

让后续业务 Skill 通过独立公共能力 `feishu.sheets.read` 读取确定的飞书 Sheets，或从 Wiki 节点在线解析其底层 Sheet。实现不复用 `lg-feishu` 的应用、Profile、Token、MCP、工具或运行时；授权控制面只签发短期令牌租约，不接收、代理或持久化工作簿内容。

首个切片提供单元格语义快照，不宣称复制飞书工作簿的视觉和交互状态：

- 包含工作簿标题、owner 和 URL。
- 包含所有未触发上限的工作表，保留顺序、隐藏状态、资源类型、网格尺寸、冻结行列和合并区间。
- 使用公式渲染模式读取范围，保留字符串、数字、布尔、空值、公式及飞书返回的其他 JSON 值。
- 不覆盖样式、条件格式、图表、批注、保护规则、嵌入媒体或 Bitable 内容。
- 不写飞书、不写本地、不建立业务内容缓存。

## OpenAPI 与最小权限

v0.4 使用飞书当前公开合同：

- `GET /open-apis/wiki/v2/spaces/get_node`：Wiki 节点解析。
- `GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token`：工作簿元数据。
- `GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query`：工作表与合并信息。
- `GET /open-apis/sheets/v2/spreadsheets/:spreadsheetToken/values_batch_get`：多范围公式和值读取。

语义能力 `feishu.sheets.read` 接受 `sheets:spreadsheet:readonly`、`sheets:spreadsheet`、`drive:drive:readonly` 或 `drive:drive` 中任一 Scope；本机固定 OAuth 组合只新增最小的 `sheets:spreadsheet:readonly`。`sheets:spreadsheet:read` 只覆盖部分工作表查询端点，不能满足元数据和值读取，因此不计为完整能力授权。

直接 Sheets 输入只请求 `feishu.sheets.read`；Wiki 输入在一个最长十分钟的任务租约中同时请求 `feishu.sheets.read` 与 `feishu.wiki.node.read`。Access Token 只驻授权控制面与当前 Provider 进程内存。

## 返回合同

MCP 工具 `feishu_sheets_read(locator, task_ref, profile_ref=None)` 返回；省略 Profile 时只自动选择本机唯一授权 Profile：

- 原始输入的标准资源定位与可选 `wiki_resolution`。
- 解析后的 `spreadsheet_token`、标题、owner、URL 和本次读取修订。
- 每个工作表的元数据、合并区间、请求范围、返回范围、修订和二维 `values`。
- 请求单元格数、实际返回值数量、工作表总数和实际返回工作表数。
- 规范化 SHA-256、观察时间、Provider 修订、完整性布尔值和逐项警告。
- 飞书正式定义的 `text`、`mention`、`url`、`formula` 复杂单元格按原始 JSON 保留；只有没有任何截断、未知类型、未知复杂单元格、缺失范围或跨请求修订变化时才返回 `ok`，否则返回 `retrieval_incomplete`。

证据哈希不包含观察时间，因而同一对象、同一修订和同一语义快照的重复读取应产生相同哈希。

## 安全上限与失败关闭

- 最多返回 100 个工作表。
- 每个普通工作表最多请求 5,000 行与 500 列。
- 单次操作最多请求 200,000 个单元格。
- 每个值接口请求最多合并 20 个范围。
- 单个值响应最多接受 10 MiB。
- 非 `sheet` 的嵌入资源只保留元数据并标记不完整，不把它当作网格读取。
- 无效工作表标识、网格合同、值二维数组或远端响应映射为稳定公共错误。
- 权限、资源不存在、限流和飞书服务错误只保留非敏感平台错误码，不透传远端消息。

## 验证门槛

- 自动化覆盖直接 Sheets、Wiki→Sheets、非 Sheet 拒绝、元数据/合并/公式/值、稳定哈希、安全上限、混合资源、权限错误、OAuth Scope、任务租约和 MCP Schema。
- 运行完整测试、Python 编译、锁文件检查和本机 preflight。
- 用户完成包含 `sheets:spreadsheet:readonly` 的增量 OAuth。
- 对目标 Wiki Sheet 连续读取两次，核对对象类型、标题、修订、工作表数量、请求/返回计数、完整性警告和证据哈希；不得在终端或最终答复中打印单元格正文、Token 或密文。

## 真实回读结果

2026-08-24 使用独立 Provider Profile `profile_00ea4619811d6fa0861a` 对 Wiki `EzhywOSQIiE92ZkHZmBcG0E9njg` 完成两次连续回读：

- Wiki 在线解析结果为 `obj_type=sheet`，工作簿标题为 `【SAMO】绿色服爬塔活动优化`。
- 两次修订均为 `91`，均返回 4/4 个工作表。
- 每次请求并返回 47,336 个单元格位置；四个工作表均为 `retrieval_complete=true`，整体状态均为 `ok`，无完整性警告。
- 两次规范化证据哈希均为 `sha256:e4148e35efff00aa33e7d0752ca006b58b3ac6a30281394e5fb1a50dcc12e345`。
- 首次真实试读识别出飞书合并区间结束索引为包含式，以及样本存在 262 列工作表；合同已据此修正并重新通过全部测试与双次真实回读。

## 后续顺序

1. 将业务快照固化能力放在调用方拥有且经用户确认的任务目录中，不让 Provider 自行写本地。
2. 补充 Sheets 样式和复杂单元格的独立语义读取合同，再决定是否将其纳入“完整来源快照”。
3. 设计 AI-owned 受管 Sheets 的预览、单次写入确认、幂等执行和同对象精确回读；不开放任意既有表格原位写入。
4. 将 Plugin 通过个人 Marketplace 安装进 Codex，并在新任务中验证命名 MCP 与 Skill 路由。
