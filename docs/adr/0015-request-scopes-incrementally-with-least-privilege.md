---
status: accepted
date: 2026-08-19
---

# 按实际语义操作增量申请最小权限

每个 Provider Profile 只获取当前实际语义操作所需的最小外部权限。飞书 Profile 初始化只请求识别用户主体和维持 OAuth 会话所必需的基础权限；任务确定读取飞书来源后，才按 Docx、Wiki、Sheets、图片或附件的实际读取需要补充缺失 Scope；任务确定飞书交付且资源类型明确后，才补充 Docx 或 Sheets 的必要写入 Scope。任务预检发现权限不足时返回 `auth_required`，明确缺失 Scope 与增量授权入口，不得静默扩大权限或一次性申请平台全部读写 Scope。

OAuth Scope 同意仅使 Profile 具备执行某类能力的资格，不会扩大 ADR 0028 定义的客户端 Profile 绑定语义能力集合，也不构成任何一次正式写入授权。即使所需写 Scope 已存在，执行客户端仍须获批对应语义能力，业务 Skill 仍必须在预览完成后取得与任务、通道、目标、操作、主体、内容哈希和预览版本绑定的单次正式写入授权。

## Considered Options

- Profile 初始化时一次性申请所有读写 Scope：拒绝，因为未使用的能力会扩大长期暴露面，也无法表达用户本次真正需要的权限。
- 由各业务 Skill 直接管理和申请 Scope：拒绝，因为授权逻辑、错误语义和凭证边界会分散到多个消费者。
- 将 OAuth 同意视为正式写入确认：拒绝，因为能力授权与一次确定内容的操作授权具有不同生命周期和审计含义。

## Consequences

- 操作类型、资源类型或 Profile 变化后必须重新执行任务预检，并重新计算缺失 Scope。
- 同一 Profile 已授予且仍有效的 Scope 可以复用，但不得据此推断用户同意本次读取目标或正式交付。
- `auth_required` 表示主体和客户端 Profile 绑定已经确定、绑定已允许所需语义能力，但执行所需外部 Scope 缺失或失效；绑定能力缺失使用 `binding_capability_required`，两者都不代表任务已获得正式写入授权。
- Provider 的能力声明必须把语义操作映射到最小权限集合；具体平台 Scope 名称属于 Provider 实现与版本化清单，不泄漏到业务 Skill 流程中。
