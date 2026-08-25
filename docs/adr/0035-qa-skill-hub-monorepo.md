---
status: accepted
date: 2026-08-25
---

# 采用 QA Skill Hub 单仓结构

项目显示名确定为 `QA Skill Hub`，稳定 ID 为 `qa-skillhub`，并以 `D:\project\work` 作为唯一 Git 根。`D:\project` 只是多个无关项目的共同父目录，不属于本项目。

业务 Skills 统一放在 `skills/<skill-name>/`，一个具名 Skill 一个目录；外部系统 Provider 的 Codex 包装继续放在 `plugins/<provider>/`，当前 Feishu Provider 源码继续保留在 `src/capability_contracts`、`src/feishu_auth_service` 和 `src/feishu_provider`。业务 Skill 即使组合飞书、SVN 等多个 Provider，也仍由自己的业务目录持有，并且只能消费 Provider 的公开合同。

本次将 `qa-case-xlsx-local` 的完整 Git 历史和当前工作快照迁入 `skills/qa-case-xlsx-local/`，不改变其纯本地行为，不创建 `qa-case-xlsx-unified`，也不提前抽取共享核心。只有出现经过验证的跨 Skill 复用后，才另行设计 `packages/<package-name>/`。

当前不创建 `workspace.yaml`。ADR-0020 中的清单方向仍可作为未来协议设计输入，但在 Schema、校验器、兼容规则和迁移流程完成前，空壳文件不能成为项目身份或能力发现真源。当前项目身份由本 ADR、根 README 和上下文地图共同明确。

## Considered Options

- 继续采用 ADR-0034 的联邦式多仓结构：拒绝，因为它把 `D:\project` 下的共同父目录误当成逻辑项目边界，无法满足业务 Skills 与能力代码由一个 Git 仓库共同提交和审查的要求。
- 在 `skills/` 中建立薄壳并从外部仓库导入实现：拒绝，因为物理路径会成为隐藏运行时依赖，安装、切换机器或清理旧目录后会失效。
- 本次同时重排 Feishu Provider 源码：拒绝，因为 Provider 目录治理与业务 Skill 迁移是不同风险面，合并执行会扩大回归范围。
- 立即建立统一用例入口和共享核心：拒绝，因为当前只有一个已实现的本地 Skill，尚无经过实现验证的第二消费者。
- 先创建最小 `workspace.yaml`：拒绝，因为仓库中没有已经实现和验证的清单协议，空文件只会制造虚假的稳定合同。

## Consequences

- 本 ADR 取代 ADR-0034；ADR-0034 继续作为历史决策保留，ADR-0002 也不恢复为当前真源。
- 公共能力上下文与各业务 Skill 上下文仍然隔离；单仓只统一版本控制、审查和路径根，不授权跨边界源码依赖。
- `plugins/workspace-feishu/skills/` 中的 Skills 是 Provider 使用指导，不是业务 Skills；生成用例、生成文档等工作流归入根 `skills/`。
- 当前远端名称 `QA-WORK.git` 不定义项目身份；远端改名作为独立操作延期，本 ADR 不授权推送或修改远端。
- 原 `D:\project\qa-case-xlsx` 暂时保留为只读恢复副本。只有新分支测试、验收和本地合并完成后，才切换用户级 Skill Junction；旧仓库清理由单独授权处理。
- 新增 SVN Provider 时应建立独立 Provider 与 Plugin；依赖飞书和 SVN 的业务 Skill 仍只负责业务编排，不持有两者的凭证或私有客户端。
