# QA Skill Hub

`QA Skill Hub`（稳定 ID：`qa-skillhub`）是位于 `D:\project\work` 的单一 Git 仓库，统一版本化业务 Skills、公共能力契约、彼此隔离的外部系统 Provider 及其 Codex Plugins。`D:\project` 只是多个项目的共同父目录，不是本项目边界。

当前公共能力实现仍是一个完全独立于 `lg-feishu` 的 Feishu `local-dev-v0` 纵切，只面向本机单用户开发验证。它已包含受控的单工作表写入，但不宣称具备正式中央部署、多人接入或任意飞书资源写入能力。

## 仓库结构

- `platform/capability-contracts/`：Provider 中立的状态、证据、能力声明和安全错误合同。
- `providers/feishu/protocol/`：授权控制面与 MCP Server 共享的 Feishu 私有通信模型。
- `providers/feishu/auth-service/`：OAuth、部署绑定、Profile 和短期租约签发。
- `providers/feishu/mcp-server/`：飞书 Docx、Wiki 与 Sheets 读取，以及受管单工作表写入 MCP；不导入授权服务实现。
- `skills/<skill-name>/`：业务 Skill 唯一源码；当前包含纯本地 `qa-case-xlsx-local` 与飞书来源/交付纵切 `qa-case-xlsx-unified`。
- `plugins/<provider>/`：可独立安装的 Codex Provider Plugin；当前包含 `workspace-feishu`。
- `docs/adr/` 与各 `CONTEXT.md`：架构决策及按领域拆分的统一语言。

根 `pyproject.toml` 是不发布的 virtual uv workspace；四个 Python 组件各自拥有包名、版本、测试和构建配置，开发态共享根 `uv.lock`。业务 Skill 使用 Codex bundled runtime，不加入 Python workspace。

业务 Skill 即使同时编排飞书、SVN 等多种能力，也仍归入自己的 `skills/<skill-name>/`；它只消费各 Provider Plugin 的公开合同，不导入 Provider 私有源码、凭证或运行时。

## 当前已实现

- 完整迁入 `skills/qa-case-xlsx-local` 的纯本地业务 Skill；其本地来源、本地 Excel 交付和无网络权限边界保持不变。
- `skills/qa-case-xlsx-unified` 首个飞书读写纵切：保留统一来源 v2 证据、复用已发布用例生成门禁，并把 `final_cases.json` 确定性转换为 Workspace Feishu A:J 单 Sheet 规范；真实交付仍以 Provider 确认和同对象回读为准。
- Provider 专用飞书企业自建应用、DPAPI 部署绑定和零输入日常启动。
- OAuth 授权码流程、单次 `state`、准入租户校验和用户身份回读。
- DPAPI 加密的 Provider Profile；只持久化 Refresh Token 密文，Access Token 仅驻进程内存。
- 自动刷新与轮换 Refresh Token，以及最长十分钟、绑定 `task_ref`、`profile_ref` 和语义能力的本机令牌租约。
- 独立 Python stdio MCP，公开八个 typed 工具：
  - `feishu_provider_manifest`
  - `feishu_resource_resolve`
  - `feishu_docx_read`
  - `feishu_sheets_read`
  - `feishu_managed_sheet_preview`
  - `feishu_managed_sheet_apply`
  - `feishu_managed_sheet_registration_resolve`
  - `feishu_managed_sheet_revise`
- 自包含 `workspace-feishu` Codex Plugin，以及彼此分离的 `feishu-read`、`feishu-write`、`feishu-auth` Skills；安装后由 Codex 从插件私有 runtime 启动 MCP，不引用仓库根目录虚拟环境。
- `profile_ref` 可省略：本机只有一个授权 Profile 时由授权控制面自动选择；没有 Profile 时返回安全、可点击的 <http://localhost:3000/oauth/start>，多个 Profile 时明确要求选择而不猜测。
- Docx 元数据和全部 Block 分页直读、修订号、内容哈希、完整性状态与安全错误映射。
- Wiki 节点在线解析；当节点真实对象为 Docx 时，在同一条用户租约中继续读取，其他对象类型明确拒绝而不猜测。
- Docx 图片与文件附件按 Block token 直接下载为受限的内存 Base64 快照，并返回媒体类型、字节数和 SHA-256；Provider 不建立正文或附件持久缓存，也不自动写入本地。
- Sheets 与 Wiki→Sheets 语义回读：返回工作簿元数据、工作表顺序/隐藏状态、网格与冻结信息、合并区间，以及带公式的单元格值；结果只驻调用内存。
- 用户指定目标的受管单 Sheet 交付：支持接管精确选择的内容空白工作表、在现有工作簿内显式新建唯一命名 Sheet，或在精确 Wiki 父节点下新建一个工作簿并接管其唯一默认 Sheet；正式写入经过十分钟不可变预览、MCP 确认、逐步检查点、前向恢复、API 回读和不落盘 XLSX 导出验证。
- 已登记工作表的同任务修订：链接唯一解析、调用方 `base_spec + next_spec`、写前双重基线验证、一次有界差异确认、退役区清理、追加式版本历史及写后双重验证；`no_change` 不确认、不写入、不增版本。

业务内容只在 Provider 与飞书 OpenAPI 之间流动。授权控制面只管理应用凭证、长期授权和短期租约，不接收或代理 Docx 正文或 Sheets 单元格内容。

## 飞书后台准备

1. 创建本项目专用的飞书企业自建应用。
2. 在“安全设置 → 重定向 URL”登记精确地址 `http://localhost:3000/callback`。
3. 在权限管理中开通并发布以下权限：
   - `tenant:tenant:readonly`（配置页自动回读企业租户）
   - `auth:user.id:read`
   - `offline_access`
   - `docx:document:readonly`
   - `wiki:node:read`
   - `wiki:node:retrieve`（完整分页读取 Wiki 父节点直接子项）
   - `wiki:node:create`（在 Wiki 父节点下新建工作簿）
   - `docs:document.media:download`
   - `sheets:spreadsheet`
   - `sheets:spreadsheet:write_only`（typed-cell 布尔值保真写入）
   - `drive:export:readonly`
4. 确保授权用户位于应用可用范围内。

本机开发纵切固定使用以下端点：

- 授权入口：`https://accounts.feishu.cn/open-apis/authen/v1/authorize`
- Token 交换与刷新：`https://open.feishu.cn/open-apis/authen/v2/oauth/token`
- 用户回读：`https://open.feishu.cn/open-apis/authen/v1/user_info`
- Wiki 节点解析：`https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node`
- Wiki 子节点分页与创建：`https://open.feishu.cn/open-apis/wiki/v2/spaces/*/nodes`
- Docx 读取：`https://open.feishu.cn/open-apis/docx/v1/documents/*`
- Docx 图片与附件下载：`https://open.feishu.cn/open-apis/drive/v1/medias/*/download`
- Sheets 元数据与工作表：`https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/*`
- Sheets 单元格批量读取：`https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/*/values_batch_get`
- Sheets 常规值、样式、尺寸、冻结与合并写入：`https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/*`
- Sheets typed-cell 布尔值保真写入：`https://open.feishu.cn/open-apis/sheet_ai/v2/spreadsheets/*/tools/invoke_write`
- 云文档导出验证：`https://open.feishu.cn/open-apis/drive/v1/export_tasks`

## 首次配置与授权

在第一个 PowerShell 终端运行：

```powershell
Set-Location -LiteralPath 'D:\project\work'
uv sync
.\scripts\configure-local-auth.ps1
```

配置页只要求填写 App ID 和 App Secret。服务会用临时 `tenant_access_token` 调用飞书租户信息接口，自动回读企业名称和 `tenant_key`，再执行“脱敏预览 → 确认 → 原子写入 → 回读”；临时 Token 不落盘。保存后脚本会继续启动 `127.0.0.1:3000` 上的授权服务；保持终端运行，在浏览器打开 <http://localhost:3000/> 并完成 OAuth。结果页出现 `verified` 和 `profile_...` 后，Provider Profile 才可用于 Docx、Wiki→Docx、Sheets 或 Wiki→Sheets 读取。

从 `0.4.x` 升级到 `0.5.x` 后，已有 Profile 需要重新走一次 OAuth，补充 `sheets:spreadsheet`、`sheets:spreadsheet:write_only` 与 `drive:export:readonly`。其中 `sheets:spreadsheet:write_only` 只用于 typed-cell 布尔值保真写入；这是 `local-dev-v0` 为完成受控写入和独立导出验证采用的固定 Scope 组合。正式中央版仍按 ADR 0015 实现任务触发的增量授权。

以后日常启动第一个终端只需：

```powershell
.\scripts\run-local-auth.ps1
```

在第二个终端启动 stdio MCP：

```powershell
Set-Location -LiteralPath 'D:\project\work'
.\scripts\run-feishu-provider.ps1
```

该脚本先验证部署绑定、至少一个授权 Profile 和本机控制面监听状态，再把标准输入输出完整留给 MCP 协议。它通常应由 MCP 宿主启动，而不是作为交互式命令使用。

## 安装公共 Plugin

仓库内个人 Marketplace 位于 `D:\project\work\.agents\plugins\marketplace.json`。在可调用 Codex CLI 的终端中执行：

```powershell
codex plugin marketplace add 'D:\project\work'
codex plugin add 'workspace-feishu@personal'
```

也可以使用交付回复中的 Codex Plugin 深链打开安装页。安装或更新后启动一个新任务，让 Codex 重新加载插件的 MCP 与 Skills。使用插件时仍需保持 `scripts\run-local-auth.ps1` 的本机授权服务运行；无需手工启动 `run-feishu-provider.ps1`，Codex 会根据 `.mcp.json` 启动插件私有 Provider。

## 配置修改与删除

先停止正常授权服务，再运行：

```powershell
.\scripts\configure-local-auth.ps1
```

管理页只允许修改 App ID 和 App Secret；准入 `tenant_key` 始终从飞书自动回读并在预览中确认，不能手工输入。回调地址、Scope、监听地址和端口固定为只读。修改时 App Secret 默认保留，只有显式选择替换才会要求两次输入。

删除必须输入精确短语 `删除本机部署绑定`。网页确认会删除本机部署绑定和本机 Provider Profiles，但不会删除飞书远端应用、轮换远端 App Secret 或撤销飞书侧用户授权。App ID 或准入租户发生变化时，旧 Profiles 会失效，用户必须重新 OAuth。

## 本机状态与密钥边界

- 部署绑定：`%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\binding.json`
- Provider Profiles：`%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\profiles\profile_*.json`
- App Secret、本机开发客户端 Secret、Refresh Token：Windows 当前用户 DPAPI 密文。
- Access Token：仅在授权控制面和当前 Provider 进程内存中短时存在；不得写入日志、任务产物或 MCP 参数。
- 配置和 Profile 文件均位于版本库之外；正常状态输出只报告脱敏就绪信息。

部署绑定 Schema 已升级到 v2，以包含本机开发客户端身份。旧的开发绑定不自动兼容；若遇到 `schema version is unsupported`，请通过配置页重新建立本机绑定并重新 OAuth。

## MCP 合同

`feishu_docx_read` 有两个必填参数和一个可选参数：

- `locator`：飞书 Docx URL、`doxcn...` / `doccn...` 文档标识，或飞书 Wiki URL。
- `task_ref`：调用方为当前逻辑任务提供的引用，不是密钥。
- `profile_ref`（可选）：OAuth 结果页返回的确定 Provider Profile 引用。省略时，中央服务只会自动选择本机唯一 Profile；零个时返回 `auth_required` 和授权网页，多个时返回 `profile_selection_required`。

Docx 输入直接读取；Wiki 输入先调用知识空间节点信息接口，验证 `obj_type=docx` 并记录 `wiki_resolution`，然后用解析出的 `obj_token` 继续读取。结构读取申请绑定 `feishu.docx.read`、必要时同时绑定 `feishu.wiki.node.read` 的任务租约；只有发现有效图片或文件附件 Block 时，才额外申请绑定 `feishu.docx.media.read` 的短期租约。返回值包含原始 Block、页数、Block 数、文档修订、媒体快照、规范化 SHA-256 证据和 `retrieval_complete`。

媒体快照只驻当前 Provider 调用内存：单项上限 8 MiB、单次调用总量上限 16 MiB、最多 64 项，并以至少 0.21 秒间隔控制下载频率。Provider 使用 Range 请求验证完整长度；超限、部分响应、无效 token 或未解析的其他内容 Block 均返回明确警告和 `retrieval_incomplete`，不会返回半截 Base64。`asset_total_bytes` 只统计完整取回的资产，证据哈希包含资产元数据与内容哈希但不重复包含 Base64。

`feishu_sheets_read` 使用相同的两个必填参数和可选 `profile_ref`；`locator` 必须是 Sheets URL 或 Wiki URL。Wiki 输入先验证 `obj_type=sheet`，并在同一租约中绑定 `feishu.wiki.node.read` 与 `feishu.sheets.read`。Provider 使用 `Formula` 渲染模式和格式化日期字符串读取工作表，返回工作簿标题、owner、工作表顺序/隐藏状态、网格、冻结行列、合并区间、请求/返回范围、修订、公式和值，以及稳定内容哈希。

`feishu_managed_sheet_preview` 接收用户指定的 Sheets/Wiki 链接、稳定 `task_ref`、可选 `profile_ref`、放置模式和 `workspace-feishu/sheet-delivery/v1` 结构化规范。`adopt_blank_sheet` 接管 URL 精确选择的内容空白工作表；无选择器时仅接受恰好一个普通可见工作表。`create_new_sheet` 必须提供唯一的 `requested_sheet_title`，URL 中的 Sheet 选择器只用于定位工作簿，不参与写入目标选择。预览只读远端状态并在本地 DPAPI/SQLite 状态库登记十分钟操作，不修改飞书。

`feishu_managed_sheet_apply` 只接受预览返回的 `operation_ref`、同一 `task_ref` 和完全相同的规范。它不提供布尔确认参数，而是在任何远端副作用前使用 MCP elicitation；拒绝、取消或客户端不支持确认都保持零写入。接受后按稳定 Sheet ID 串行写值/公式、清理并设置交付矩形样式、行列尺寸、冻结与合并。结果不明时不自动重发、删除、清空或回滚，只保留现场并按同一操作前向恢复。只有 API 值/结构回读和同工作簿临时 XLSX 导出均验证通过，状态才是 `delivered`。

`feishu_managed_sheet_registration_resolve` 只读解析用户提供的 Sheets/Wiki 链接，并在选定 Profile 下唯一匹配已有受管登记；工作簿链接无 Sheet 选择器时只允许恰好一条登记。它返回稳定 `registration_ref`、当前 `managed_version`、规范摘要与刷新后的展示元数据，不产生写入授权。

`feishu_managed_sheet_revise` 是一体化修订工具，接收 `registration_ref`、稳定 `task_ref`、调用方持有的完整 `base_spec` 和 `next_spec`。它先对同一 Sheet ID 完成 API + 临时 XLSX 双重基线验证；无变化返回 `no_change`，有变化只显示一次 MCP 差异确认。缩表会清空 `base_rect - next_rect` 的内容与样式，并把完全退役行列重置为 `24 px` / `100 px`，但不删除网格轴。只有写后 API/XLSX 验证完整且版本指针原子前移后才返回 `delivered`。相同规范对重试复用原修订操作，不要求用户保存 `operation_ref`。

Docx 与 Sheets 工具以同一个顶层结构化结果返回成功或失败，不要求调用方解包额外 `result` 字段。`auth_required` 只暴露本机生成的授权 URL 和经过白名单校验的能力列表；Provider 不信任或转发控制面响应中的任意 URL、Secret 或远端错误正文。

Sheets 读取安全上限为最多 100 个工作表、每表 5,000 行与 500 列、单次调用总计 200,000 个请求单元格、每批最多 20 个范围，单个飞书值响应最多 10 MiB。飞书正式定义的 `text`、`mention`、`url`、`formula` 复杂值按原始 JSON 完整保留；超限、混合 Bitable 等不支持的工作表类型、未知复杂单元格类型、缺失范围或读取过程中修订变化都会明确返回 `retrieval_incomplete`。

写入规范最多 5,000 行、100 列、200,000 个交付单元格和 500 个结构范围。当前受控映射只支持无边框或完整边框，且不支持自动换行；无法由公开 API 设置并由 XLSX 精确验证的样式会返回 `unsupported_delivery_spec`，不会静默忽略。操作状态库位于 `%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\operations-v1.sqlite3`，文件名保持兼容，内部 Schema v3 保存首次交付、受管版本元数据、修订锁与检查点；目标元数据使用当前 Windows 用户 DPAPI 加密，业务单元格正文不进入数据库。

## 验证

```powershell
uv run pytest
uv run python -m compileall -q platform/capability-contracts/src providers/feishu
uv lock --check
.\scripts\install-personal-skills.ps1 -Check
.\scripts\verify-repo.ps1
uv run --package workspace-feishu-auth-service --locked feishu-auth preflight
```

`preflight` 不输出 App Secret、Refresh Token、Access Token 或本机客户端 Secret。

## 尚未实现

- 正式中央控制面的稳定 HTTPS、mTLS 客户端身份、客户端 Profile 浏览器绑定、持久审计、撤销和多人隔离。
- Wiki 中 Docx/Sheets 以外对象的内容读取、Docx 中嵌入 Sheets 等复合 Block 解析、Docx 写入，以及任意已有非空 Sheet 覆盖。
- Sheets 自动换行、局部边框、图表、批注和嵌入媒体语义。
- 将正文、图片、附件和表格快照固化到调用方拥有的任务隔离暂存区；当前 v0.4 只返回内存快照，不宣称已经形成持久任务包。
- 正式多人模式下的用户/客户端/任务身份绑定、中央审计，以及独立的放弃或远端清理流程。
- 正式 Marketplace 发布流水线、插件自动升级和跨机器安装验证。
- SVN 等新增 Provider、统一入口的本地/飞书混合来源、跨任务交付基线，以及经过独立设计的共享业务包。

这些能力会继续复用 `capability_contracts` 的能力声明、结构化状态、证据和安全错误语义；资源定位、资源类型、身份、MCP 和远端协议由各 Provider 独立拥有。
