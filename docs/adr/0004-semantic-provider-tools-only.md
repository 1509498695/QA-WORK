---
status: accepted
date: 2026-08-19
---

# Provider 只暴露受控语义工具

外部系统 Provider 通过各自 Plugin/MCP 暴露具名、版本化的受控语义工具，业务 Skill 不得传入任意 HTTP 方法、OpenAPI 路径或原始请求体。飞书 Provider 的初始能力按资源解析、完整来源快照、写入预览、经确认的幂等应用和写后精确回读等语义划分；分页、限流、重试、权限检查、远端对象差异和结果归一化全部留在 Provider 内部。未覆盖的新需求通过扩展并版本化语义契约解决，不得以原始 OpenAPI/HTTP 代理作为旁路。

## Considered Options

- 暴露通用 OpenAPI/HTTP 代理：拒绝，因为业务 Skill 会各自处理权限、分页、限流、幂等与错误语义，并获得超出业务意图的任意调用面。
- 只提供一个通用 `read` 和一个通用 `write`：拒绝，因为来源快照、预览、应用和回读具有不同的授权与证据状态，合并后无法可靠审计。

## Consequences

- Provider 工具参数和结果不得暴露访问令牌、应用密钥或 Provider 私有资源绑定。
- 业务 Skill 只能根据稳定结果状态编排流程，不得依赖飞书原始响应结构。
