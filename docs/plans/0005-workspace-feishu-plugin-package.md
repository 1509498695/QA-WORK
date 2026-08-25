# Workspace Feishu 独立 Plugin 首版

## 状态

v0.4.1 的插件源、私有 runtime、MCP 清单、两个 Skills、授权恢复合同和真实 Sheets 回归已完成。插件清单与 Skill 均通过官方本地校验器；Codex 安装与新任务加载仍需通过 Marketplace 深链或可用的 Codex CLI 完成。

## 决策

- 插件名固定为 `workspace-feishu`，MCP server 名固定为 `workspace_feishu`。
- 插件自带 `runtime/pyproject.toml`、`runtime/uv.lock` 和三组 Python 包；不得引用 `D:\project\work\.venv`，也不得引用或复用 `lg-feishu`。
- 根目录 `src/` 是开发源，`plugins/workspace-feishu/scripts/sync_runtime.ps1` 只做确定的 Python 源码同步；发现目标中存在来源不明的陈旧 Python 文件时失败关闭，不自动删除。
- v0.4.1 只声明 `Read` 与 `Interactive`，不声明 `Write`。
- `feishu-read` 负责 Docx、Wiki、Sheets 的只读路由和证据判定；`feishu-auth` 负责 OAuth 恢复链接。业务 Skill 后续只消费公共 MCP 合同。

## 本地 Profile 路由

读取工具保留可选 `profile_ref`，但正常对话无需用户输入：

- 本机正好一个 Profile：授权控制面自动选择，并在读取结果中回传实际 `profile_ref`。
- 本机没有 Profile：返回 `auth_required`，并附带由本机端口生成的 `http://localhost:3000/oauth/start`。
- 本机多个 Profile：返回 `profile_selection_required` 和非敏感 Profile 引用，调用方必须请用户选择。

这保证首版本机零输入，同时不把某个用户的 Profile 写死进公共插件，并为后续多人身份路由保留明确迁移点。

## 结构化失败合同

FastMCP 会给顶层联合类型自动包裹 `result`。v0.4.1 使用 Pydantic `RootModel` 表达“读取成功或公共失败”，使成功和失败都保持现有顶层字段合同。授权失败只允许传播白名单能力名；授权 URL 始终从受信本机控制面地址派生，不采信上游响应给出的 URL 或敏感详情。

## 验证证据

- 根项目：78 项自动化测试通过，只有两个已知上游弃用/类型告警。
- Plugin、`feishu-read`、`feishu-auth`：本地校验器通过。
- 插件私有 runtime 经真实 stdio MCP 握手，公开四个只读工具，全部带输出 Schema，Provider 版本为 `0.4.1`。
- 省略 `profile_ref` 读取 Wiki Sheet：自动选择 `profile_00ea4619811d6fa0861a`，修订 `91`，4/4 工作表，47,336/47,336 单元格位置，状态 `ok`，哈希 `sha256:e4148e35efff00aa33e7d0752ca006b58b3ac6a30281394e5fb1a50dcc12e345`。
- 省略 `profile_ref` 读取直接 Sheet：自动选择同一 Profile，修订 `112`，4/4 工作表，13,420/13,420 单元格位置，状态 `ok`，哈希 `sha256:a78766d4ba4fd425d3ea5b76e034cbcf9621e928a29ff01122afba97eacc1bb9`。
- 使用不存在的合法格式 Profile 请求时，MCP 顶层返回 `auth_required` 与可点击授权入口，不包含 Token、Secret 或不受信 URL。

## 下一阶段

1. 通过个人 Marketplace 安装插件，在新 Codex 任务中确认 `mcp__workspace_feishu__*` 与两个 Skill 真正加载。
2. 新建一个最小业务路由 Skill：用户输入飞书 URL 时走本插件；输入本地路径时走本地能力；任何本地业务写入前必须展示目标和内容摘要并取得用户确认。
3. 在读取合同稳定后，设计 AI-owned 飞书写入能力，采用预览、单次确认、幂等写入和同对象语义回读；不得直接开放任意既有资源写入。
4. 复用 Provider/Plugin/Skill 分层接入 SVN 只读 Provider，再评估中央 HTTPS、多人身份隔离、审计与撤销。
