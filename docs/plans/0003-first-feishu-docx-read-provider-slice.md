# 首个 Feishu Docx 与 Wiki→Docx 只读 Provider 纵切

## 决策状态

本计划中的首版边界已经逐项确认并实现为 `local-dev-v0`。它用于先跑通本机真实授权、长期授权刷新、短期租约和 Docx 数据面直读，不替代 ADR 0024 至 ADR 0029 定义的正式中央服务身份与绑定模型。

## 本轮目标

让后续业务 Skill 只依赖公共语义合同即可读取确定的飞书 Docx，或从 Wiki 节点解析并读取其底层 Docx；不复用 `lg-feishu` 的应用、Profile、Token、MCP、工具或运行时，并保证授权控制面不接触文档正文。

## 已确认架构

```text
业务 Skill
    │ typed MCP: locator + task_ref + profile_ref
    ▼
Feishu Provider 执行客户端 ───────────────► 飞书 Wiki / Docx / Media OpenAPI
    │                                          │
    │ 本机短期令牌租约                          │ Wiki 节点解析 / Block 分页
    ▼                                          ▼
飞书授权控制面                           结构化快照与读取证据
    │
    ├─ 部署绑定：App ID / App Secret / tenant_key
    └─ Provider Profile：加密 Refresh Token / Scope / 用户主体
```

- 授权控制面拥有 OAuth、长期凭证和 Token 刷新，不代理 Docx 正文。
- Provider 为直接 Docx 读取请求 `feishu.docx.read`；为 Wiki→Docx 读取在同一租约中同时请求 `feishu.wiki.node.read` 与 `feishu.docx.read`。只有返回有效图片或文件附件 Block 时才单独请求 `feishu.docx.media.read`。两类租约最长十分钟；Access Token 只驻进程内存。
- Provider 取得租约后直接调用飞书 Wiki/Docx API，并通过 stdio MCP 返回 typed 结果。
- `capability_contracts` 只承载公共资源定位、能力声明、状态、证据和错误语义，不承载飞书 API 细节。
- 本地文件不是 Provider；未来本地读写与飞书、SVN 等 Provider 共享入口编排，但保持不同执行和授权边界。

## `local-dev-v0` 与 `central-v1` 边界

| 边界 | `local-dev-v0` | `central-v1` |
|---|---|---|
| 控制面位置 | 当前 Windows 用户本机 | 稳定 HTTPS 独立服务 |
| 客户端认证 | 同机单客户端受保护凭证 | 每客户端证书与 mTLS |
| Profile 使用权 | 当前准入租户下显式 `profile_ref` | 浏览器确认的客户端 Profile 能力绑定 |
| 状态存储 | DPAPI 保护的绑定与 Profile 文件 | 集中密钥设施与持久状态 |
| 租约 | 绑定任务、Profile、能力，最长十分钟 | 加入租户、客户端、绑定修订和审计 |
| 用户规模 | 单机单管理员开发验证 | 多人、多设备、可撤销隔离 |

`local-dev-v0` 的本机客户端凭证是明确的临时替代，不得被文档、代码或部署声明为 mTLS 等价物。

## 已实现合同

### `feishu_provider_manifest`

返回 Provider 版本、公共合同版本、资源类型、操作集合和 `development_only` 状态。

### `feishu_resource_resolve`

只分类 Windows 本地路径、飞书 Docx/Wiki/Sheets URL 和 Docx 资源标识，不读取内容，也不授予任何访问权。

### `feishu_docx_read`

输入：

- `locator`
- `task_ref`
- `profile_ref`

输出：

- 文档 ID、标题和修订号。
- Wiki 输入的节点 token、空间、真实对象类型、真实对象 token 和节点元数据；只有 `obj_type=docx` 才继续读取。
- 全部已返回的原始 Blocks、Block 数和页数。
- 图片与文件附件的内存快照：Block 关联、媒体类型、字节数、内容 SHA-256 和完整内容 Base64；不自动落盘。
- 资产数量、完整资产总字节数，以及逐项超限或部分响应警告。
- 观察时间、规范化内容哈希、Provider 修订和完整性警告。
- `ok` 或 `retrieval_incomplete`，不把超限、半截媒体或尚未解析的复合 Block 误报为完整。

## 安全与失败合同

- App Secret、本机客户端 Secret 和 Refresh Token 只以当前 Windows 用户 DPAPI 密文持久化。
- OAuth Code、明文 Token 和远端错误正文不进入普通日志、状态页、MCP 参数或测试输出。
- 租约请求要求本机客户端身份；未配置、身份不匹配、Profile 不存在、Scope 不足或刷新失败均失败关闭。
- Docx 分页必须得到新的页令牌，并有页数上限；重复、缺失或无限分页返回 `retrieval_incomplete`。
- 媒体单项上限 8 MiB、单次总量上限 16 MiB、最多 64 项；下载间隔至少 0.21 秒。Range 响应必须证明覆盖完整对象，否则不返回部分 Base64。
- Provider 不持久化正文、图片或附件。v0.3 只提供调用内内存快照；调用方任务隔离暂存仍按 ADR 0016/0019 后续实现。
- 平台拒绝映射为稳定公共错误；只保留非敏感平台错误码，不透传远端消息。
- Wiki 节点权限拒绝、节点不存在、无效响应和非 Docx 目标分别映射为稳定错误；不得把 Wiki token 直接当作文档 token。
- App ID 或准入租户变化会使全部旧 Profiles 失效并要求重新 OAuth。

`local-dev-v0` 为完成首个真实纵切，OAuth 固定请求身份、离线刷新、Docx 只读、Wiki 节点只读和文档媒体下载 Scope。已有 Profile 升级到 v0.3 后必须重新 OAuth。ADR 0015 所定义的按任务增量补 Scope 仍属于正式中央版工作，不因本机固定组合而废止。

## 验证状态

- 64 项自动化测试通过；Python 编译与依赖同步通过，仅保留两条上游依赖警告。
- 已覆盖 OAuth、DPAPI 绑定、Profile 加密与轮换、十分钟租约、媒体语义能力/Scope、客户端鉴权、Wiki 不透明 Docx token、非 Docx 拒绝、安全字符校验、Docx 分页、完整 200/206 媒体响应、超限/数量限制、无效媒体 Block、权限错误脱敏、MCP Schema/只读注解和管理配置失效。
- 当前机器的部署绑定与 `profile_00ea4619811d6fa0861a` 已完成包含 `wiki:node:read` 和 `docs:document.media:download` 的真实 OAuth。
- 已通过公共 `feishu_docx_read` 对 Wiki `KhbDwPjf9iovDnkD3yscx9M8nAb` 完成两次同对象 v0.3 回读：节点解析为 `docx`，修订 `137`，56 个 Block、1 页，结果均为 `ok` 且无完整性警告。
- 该对象的 2 张图片均已完整读入内存，分别为 57,503 和 116,482 字节，总计 173,985 字节；逐项 Base64 解码长度与声明字节数一致。两次整体证据 SHA-256 均为 `d43f69147fad31c2c040d8fcbeb718b6fa0a424d073ee4e159eca3ea081b9368`，两张图片的内容哈希、大小和文档修订也逐项一致。
- 当前真实对象没有文件附件；文件附件下载、哈希、Base64、超限和错误路径已由自动化测试覆盖，但在取得真实附件样本前不宣称完成真实附件回读验证。

## 后续顺序

1. 实现调用方拥有的任务隔离暂存，把内存媒体快照固化为可审计任务包，同时维持所有本地写入前确认。
2. 将 Provider MCP 打包为独立 Plugin，再让第一个业务 Skill 只通过公共合同消费。
3. 实现本地文件能力与对话路由：输入定位符自动判断通道，模糊时询问，所有本地写入在确定路径和内容预览后由用户确认。
4. 在开始多人接入前实现 `central-v1` 的 HTTPS、mTLS、客户端登记、客户端 Profile 能力绑定、审计和撤销。
5. 以相同 Provider 边界新增 SVN 只读能力，不把 SVN 凭证或协议并入 Feishu Provider。
