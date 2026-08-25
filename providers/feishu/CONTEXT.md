# Feishu Provider 领域语言

本上下文定义 Feishu Provider 内授权控制面、执行客户端和只读资源操作共同使用的语言。

## 身份与授权

**Feishu Provider**：
独立管理飞书连接、身份、权限与只读资源语义，并以公共能力契约返回状态和证据的能力 Provider。
_Avoid_：lg-feishu、业务 Skill、通用飞书脚本

**飞书租户**：
企业自建应用、用户身份和资源可见范围共同归属的企业边界。
_Avoid_：用户 Profile、知识空间、群聊

**Provider 专用应用**：
只由 Feishu Provider 使用、承载 OAuth 应用身份和回调配置的飞书企业自建应用。
_Avoid_：其他飞书 Provider 的应用、用户身份、业务 Skill 专用应用

**部署绑定**：
Provider 专用应用、准入租户与本地执行客户端身份之间经管理员确认建立的完整配置关系。
_Avoid_：环境变量片段、单个 App Secret、OAuth 用户授权

**Provider Profile**：
一个飞书用户主体及其长期 OAuth 授权状态的稳定逻辑引用；Profile 不向执行客户端暴露 Refresh Token。
_Avoid_：账号密码、最近登录用户、Access Token

**OAuth Scope**：
飞书平台授予一个 Profile 的原子权限；Provider 语义能力必须映射到一个或多个可接受 Scope。
_Avoid_：业务权限、正式写入确认、能力 ID

**语义能力 ID**：
Feishu Provider 对稳定用户意图的私有标识，用于租约申请和 Scope 覆盖判断。
_Avoid_：飞书 Scope、MCP 工具名、HTTP 路径

## 部署单元

**授权控制面**：
持有 Provider 应用身份和 Profile 长期授权、完成 OAuth 生命周期并签发短期租约的安全边界；它不接收或代理文档与表格正文。
_Avoid_：MCP Server、业务数据网关、OAuth 结果页

**MCP Server**：
取得短期租约后直接调用飞书 OpenAPI、读取目标资源并返回结构化快照与证据的执行边界。
_Avoid_：授权控制面、Profile 仓库、远端内容缓存

**执行客户端身份**：
MCP Server 向授权控制面证明自身获准申请租约的本地身份；它与飞书用户身份彼此独立。
_Avoid_：Provider Profile、飞书 Access Token、调用方任务引用

**Feishu 私有协议**：
授权控制面与 MCP Server 共享的 capability ID、租约请求、租约交付和执行客户端身份模型。
_Avoid_：公共能力契约、远端 OpenAPI Schema、服务实现

**短期访问令牌租约**：
授权控制面为确定任务、Profile 和语义能力签发的短时访问能力；Access Token 只在授权控制面与当前 MCP 进程内存中存在。
_Avoid_：Refresh Token、跨任务会话、长期凭证副本

## 飞书资源

**飞书资源定位符**：
Feishu Provider 可识别并规范化的 Docx 标识、Docx URL、Wiki URL 或 Sheets URL。
_Avoid_：任意网页地址、任务引用、读取结果

**Docx 资源**：
由文档元数据、完整 Block 序列以及可读取图片和附件组成的飞书文档对象。
_Avoid_：旧版 Docs 推断、Wiki 节点、附件缓存目录

**Wiki 节点**：
知识空间中的导航对象；只有在线解析其真实对象类型和 token 后，才能继续读取对应资源。
_Avoid_：Docx 别名、Sheets 别名、静态 URL 推断

**Sheets 资源**：
由工作簿元数据、有序工作表、网格边界、合并区间、公式和值组成的飞书电子表格对象。
_Avoid_：Bitable、视觉工作簿克隆、本地 Excel 文件

**资源解析**：
在不读取正文的情况下识别定位符目标、资源类型和规范标识的操作；Wiki 的真实对象解析属于后续在线步骤。
_Avoid_：内容读取、权限预检、URL 字符串截取即完成

**只读资源快照**：
一次调用中从飞书实际对象读取的结构化内容、修订和完整性证据；快照不由 Provider 跨任务持久化。
_Avoid_：实时链接、Provider 缓存、业务交付物

**读取不完整**：
资源存在未支持对象、未解析内容、安全上限截断、分页异常或修订变化，使快照不能证明覆盖目标全部语义的状态。
_Avoid_：读取失败、可忽略警告、部分成功即完整
