---
status: superseded by ADR-0034
date: 2026-08-19
---

# 单仓管理并保持 Provider 独立发布边界

`D:\project\work` 采用单一 Git monorepo，按 `contracts/`、`providers/<provider>/` 和 `skills/<skill>/` 划分目录；共享契约、Provider 与消费者可以在同一次变更中完成兼容修改和验证。每个 Provider 仍必须拥有独立的 Plugin 清单、版本、配置、凭证、工具命名和发布门禁，除 Provider 中立公共包外不得跨 Provider 导入实现；单仓只统一源码治理，不把多个外部系统合并成同一运行时或权限主体。

## Considered Options

- 在工作空间根目录下维护多个独立 Git 仓库：拒绝，因为公共契约早期演进需要跨仓协调版本、提交顺序与兼容测试。
- 把全部 Provider 合并为一个 Plugin/MCP 包：拒绝，因为这会破坏已经确认的身份、权限、发布和故障隔离边界。

## Consequences

- 现有 `qa-case-xlsx-local` 只有在单独确认迁移与历史保留方案后才进入 `skills/`，本 ADR 不授权复制、移动或改写其仓库。
- 将来若拆分仓库，必须先发布稳定契约版本并证明各消费者可以独立升级。
