---
status: accepted
date: 2026-08-25
---

# 不创建自定义 Workspace 清单

QA Skill Hub 当前不创建 `workspace.yaml`。Python 成员由根 `pyproject.toml` 的 uv Workspace 表达，Plugin 由 `.agents/plugins/marketplace.json` 与各自 `plugin.json` 表达，业务 Skill 由 `SKILL.md` 与 `agents/openai.yaml` 表达，领域关系由 `CONTEXT-MAP.md` 表达；再增加无人消费的自定义清单只会形成重复真源。

本 ADR 取代基于跨仓联邦前提的 ADR-0020。只有未来出现已经实现的运行时消费者，确实需要标准文件无法表达的任务所有权、合同兼容范围或成员绑定时，才根据真实 Schema、校验器和迁移流程重新提出清单设计。
