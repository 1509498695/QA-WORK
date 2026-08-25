# Context Map

## Contexts

- [公共能力平台](./platform/CONTEXT.md) — 定义公共能力契约，并治理彼此隔离的外部系统 Provider
- [qa-case-xlsx-local](./skills/qa-case-xlsx-local/CONTEXT.md) — 只读取本地来源并交付本地 Excel 的独立业务 Skill

## Relationships

- `D:\project\work` 是 `QA Skill Hub`（`qa-skillhub`）的单一 Git 根；`D:\project` 只是其他项目共用的父目录。
- 每个业务 Skill 在 `skills/<skill-name>/` 内保留自己的领域语言、流程、证据模型、测试和交付格式。
- 业务 Skill 需要飞书、SVN 等外部能力时，只通过对应 Provider Plugin 的公开契约消费；同仓目录邻近不授权导入 Provider 私有实现。
- `qa-case-xlsx-local` 是纯本地业务 Skill，不消费 Workspace Feishu 或其他网络 Provider。
- 公共能力平台不得依赖任何业务 Skill 的流程、证据模型或交付格式。
- Provider Plugin 内的指导 Skills 属于 Provider 包装，不是业务 Skill。

## Reference Assessments

- [QAWORK 架构参考边界](./docs/reference/qawork-architecture-reference.md) — 记录可借鉴的合同模式及明确禁止复制的业务和 Provider 依赖
