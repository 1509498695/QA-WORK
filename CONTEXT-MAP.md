# Context Map

## Contexts

- [公共能力](./platform/CONTEXT.md) — 定义业务 Skill 与外部系统 Provider 共享的中立操作、状态和证据语言。
- [Feishu Provider](./providers/feishu/CONTEXT.md) — 定义飞书身份、授权、租约和只读资源语义。
- [qa-case-xlsx-local](./skills/qa-case-xlsx-local/CONTEXT.md) — 定义本地策划案、本地测试用例和 Excel 审计交付。

## Relationships

- Feishu Provider 实现公共能力契约，同时独立拥有飞书资源类型、定位规则和私有授权协议。
- Feishu 私有协议只在授权控制面与 MCP Server 之间使用，不对业务 Skill 构成公共合同。
- 业务 Skill 只能消费 Provider 的公开语义操作，不导入 Provider 私有源码、身份或协议。
- `qa-case-xlsx-local` 当前保持纯本地边界，不消费 Feishu Provider 或其他网络 Provider。
- 公共能力上下文不依赖任何业务 Skill 的来源模型、规则、用例字段或交付格式。

## Reference Assessments

- [QAWORK 架构参考边界](./docs/reference/qawork-architecture-reference.md) — 记录可借鉴的合同模式和明确禁止复制的依赖。
