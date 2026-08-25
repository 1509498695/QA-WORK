# QA Skill Hub

`QA Skill Hub`（稳定 ID：`qa-skillhub`）是位于 `D:\project\work` 的单一 Git 仓库，统一版本化业务 Skills、公共能力契约、彼此隔离的外部系统 Provider 及其 Codex Plugins。`D:\project` 只是多个项目的共同父目录，不是本项目边界。

当前公共能力实现仍是一个完全独立于 `lg-feishu` 的 Feishu `local-dev-v0` 纵切，只面向本机单用户开发验证，不宣称已经具备正式中央部署、多人接入或写入能力。

## 仓库结构

- `skills/<skill-name>/`：一个具名业务 Skill 一个目录；当前包含纯本地 `qa-case-xlsx-local`。
- `plugins/<provider>/`：按外部系统独立安装和发布的 Codex Provider Plugin；当前包含 `workspace-feishu`。
- `src/`：当前公共契约、授权服务和 Feishu Provider 源码；本次迁移不重排。
- `packages/`：未来稳定共享业务代码的位置；只有形成真实复用合同后才创建，不预建空壳。
- `docs/` 与 `platform/`：跨上下文架构决策、实施计划和公共能力领域语言。

业务 Skill 即使同时编排飞书、SVN 等多种能力，也仍归入自己的 `skills/<skill-name>/`；它只消费各 Provider Plugin 的公开合同，不导入 Provider 私有源码、凭证或运行时。

## 当前已实现

- 完整迁入 `skills/qa-case-xlsx-local` 的纯本地业务 Skill；其本地来源、本地 Excel 交付和无网络权限边界保持不变。
- Provider 专用飞书企业自建应用、DPAPI 部署绑定和零输入日常启动。
- OAuth 授权码流程、单次 `state`、准入租户校验和用户身份回读。
- DPAPI 加密的 Provider Profile；只持久化 Refresh Token 密文，Access Token 仅驻进程内存。
- 自动刷新与轮换 Refresh Token，以及最长十分钟、绑定 `task_ref`、`profile_ref` 和语义能力的本机令牌租约。
- 独立 Python stdio MCP，公开 typed、read-only 工具：
  - `feishu_provider_manifest`
  - `feishu_resource_resolve`
  - `feishu_docx_read`
  - `feishu_sheets_read`
- 自包含 `workspace-feishu` Codex Plugin，以及彼此分离的 `feishu-read`、`feishu-auth` Skills；安装后由 Codex 从插件私有 runtime 启动 MCP，不引用仓库根目录虚拟环境。
- `profile_ref` 可省略：本机只有一个授权 Profile 时由授权控制面自动选择；没有 Profile 时返回安全、可点击的 <http://localhost:3000/oauth/start>，多个 Profile 时明确要求选择而不猜测。
- Docx 元数据和全部 Block 分页直读、修订号、内容哈希、完整性状态与安全错误映射。
- Wiki 节点在线解析；当节点真实对象为 Docx 时，在同一条用户租约中继续读取，其他对象类型明确拒绝而不猜测。
- Docx 图片与文件附件按 Block token 直接下载为受限的内存 Base64 快照，并返回媒体类型、字节数和 SHA-256；Provider 不建立正文或附件持久缓存，也不自动写入本地。
- Sheets 与 Wiki→Sheets 语义回读：返回工作簿元数据、工作表顺序/隐藏状态、网格与冻结信息、合并区间，以及带公式的单元格值；结果只驻调用内存。

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
   - `docs:document.media:download`
   - `sheets:spreadsheet:readonly`
4. 确保授权用户位于应用可用范围内。

本机开发纵切固定使用以下端点：

- 授权入口：`https://accounts.feishu.cn/open-apis/authen/v1/authorize`
- Token 交换与刷新：`https://open.feishu.cn/open-apis/authen/v2/oauth/token`
- 用户回读：`https://open.feishu.cn/open-apis/authen/v1/user_info`
- Wiki 节点解析：`https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node`
- Docx 读取：`https://open.feishu.cn/open-apis/docx/v1/documents/*`
- Docx 图片与附件下载：`https://open.feishu.cn/open-apis/drive/v1/medias/*/download`
- Sheets 元数据与工作表：`https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/*`
- Sheets 单元格批量读取：`https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/*/values_batch_get`

## 首次配置与授权

在第一个 PowerShell 终端运行：

```powershell
Set-Location -LiteralPath 'D:\project\work'
uv sync
.\scripts\configure-local-auth.ps1
```

配置页只要求填写 App ID 和 App Secret。服务会用临时 `tenant_access_token` 调用飞书租户信息接口，自动回读企业名称和 `tenant_key`，再执行“脱敏预览 → 确认 → 原子写入 → 回读”；临时 Token 不落盘。保存后脚本会继续启动 `127.0.0.1:3000` 上的授权服务；保持终端运行，在浏览器打开 <http://localhost:3000/> 并完成 OAuth。结果页出现 `verified` 和 `profile_...` 后，Provider Profile 才可用于 Docx、Wiki→Docx、Sheets 或 Wiki→Sheets 读取。

从 `0.3.0` 升级到 `0.4.x` 后，已有 Profile 需要重新走一次 OAuth，补充 `sheets:spreadsheet:readonly`。这是 `local-dev-v0` 为尽快完成真实纵切采用的固定只读 Scope 组合；正式中央版仍按 ADR 0015 实现任务触发的增量授权。

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

Docx 与 Sheets 工具以同一个顶层结构化结果返回成功或失败，不要求调用方解包额外 `result` 字段。`auth_required` 只暴露本机生成的授权 URL 和经过白名单校验的能力列表；Provider 不信任或转发控制面响应中的任意 URL、Secret 或远端错误正文。

Sheets 安全上限为最多 100 个工作表、每表 5,000 行与 500 列、单次调用总计 200,000 个请求单元格、每批最多 20 个范围，单个飞书值响应最多 10 MiB。飞书正式定义的 `text`、`mention`、`url`、`formula` 复杂值按原始 JSON 完整保留；超限、混合 Bitable 等不支持的工作表类型、未知复杂单元格类型、缺失范围或读取过程中修订变化都会明确返回 `retrieval_incomplete`。v0.4 不读取样式、图表、批注和嵌入媒体，因此该能力是可审计的单元格语义快照，不是视觉工作簿克隆。

## 验证

```powershell
uv run pytest
uv run python -m compileall -q src tests
uv lock --check
uv run feishu-auth preflight
```

`preflight` 不输出 App Secret、Refresh Token、Access Token 或本机客户端 Secret。

## 尚未实现

- 正式中央控制面的稳定 HTTPS、mTLS 客户端身份、客户端 Profile 浏览器绑定、持久审计、撤销和多人隔离。
- Wiki 中 Docx/Sheets 以外对象的内容读取、Docx 中嵌入 Sheets 等复合 Block 解析、Sheets 写入、Docx 写入。
- Sheets 样式、图表、批注和嵌入媒体语义。
- 将正文、图片、附件和表格快照固化到调用方拥有的任务隔离暂存区；当前 v0.4 只返回内存快照，不宣称已经形成持久任务包。
- 用户通过对话选择飞书或本地目标，以及所有本地写入前的确定内容确认。
- 正式 Marketplace 发布流水线、插件自动升级和跨机器安装验证。
- SVN 等新增 Provider、更多业务 Skill，以及经过独立设计的共享业务包。

这些能力会继续复用 `capability_contracts` 的资源定位、能力声明、结构化状态和证据语义，但各 Provider 仍独立拥有配置、身份、MCP 和远端协议。
