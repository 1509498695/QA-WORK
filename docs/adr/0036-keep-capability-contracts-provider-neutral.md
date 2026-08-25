---
status: accepted
date: 2026-08-25
---

# 公共能力契约保持 Provider 中立

`platform/capability-contracts` 只定义跨 Provider 稳定的能力声明、操作状态、证据和错误语义，不枚举飞书、SVN 等外部系统的资源类型，也不解析其 URL 或资源标识。现有 `FEISHU_*` 类型、飞书域名识别和 `resolve_feishu_docx` 等定位逻辑迁入 `providers/feishu`，由 Feishu Provider 独立拥有并通过公开工具结果适配通用合同。

拒绝把公共契约建设成所有 Provider 的中央资源注册表，因为每新增或修改一个 Provider 都会迫使无关 Provider 与业务 Skill 升级公共包，并重新形成跨 Provider 发布耦合。迁移必须保持当前 MCP 序列化值和错误语义兼容，并用合同测试证明业务消费者不依赖被移出的飞书私有实现。
