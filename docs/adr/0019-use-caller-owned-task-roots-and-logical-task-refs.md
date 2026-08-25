---
status: accepted
date: 2026-08-19
---

# 调用方持有任务目录并通过逻辑任务引用访问

每个业务任务的物理目录归调用方 Skill 或其所属 Workspace 持有，不强制集中到 `D:\project\work\tasks`。共享 `packages/local-artifacts/` 是任务路径边界的唯一解析者：它只在调用方已登记并通过校验的任务根内创建任务，写入版本化 manifest，并签发逻辑 `task_ref`。业务 Skill 和 Provider 后续都通过 `task_ref` 与命名构件分区访问任务，不得把用户输入或业务计划中的任意绝对路径直接当作读写目标。

首版标准任务布局包含根 manifest 及 `sources/`、`normalized/`、`outputs/`、`audit/`、`staging/`。manifest 至少标识任务合同与 schema 版本、稳定任务 ID、所有者 Workspace、所有者 Skill、创建时间和生命周期状态；快照版本、Profile 绑定、预览与授权摘要、交付状态、清理状态和恢复墓碑通过同一版本化任务合同关联。物理根路径由本地构件能力解析，不进入 Provider 公共业务 payload。

Provider 只能使用公共任务合同允许的命名分区和操作。例如来源读取可以向当前任务的 `sources/` 与 `normalized/` 写入快照，交付预览可以读取确定的 `outputs/`，审计结果写入 `audit/`；它不能遍历任务根、访问其他任务或把 `task_ref` 当作宽泛文件系统权限。本地构件能力在每次操作时重新解析真实路径，拒绝符号链接、Junction、重解析点、边界漂移和哈希前置条件不匹配。

## Considered Options

- 所有 Skill 共用 `D:\project\work\tasks`：拒绝，因为业务 Skill 可以位于其他目录或 Workspace，集中物理目录会改变内容所有权并扩大公共工作空间的数据聚合范围。
- Provider 直接接收绝对输入和输出路径：拒绝，因为用户输入、历史计划或被篡改任务文件可能造成越界读取、覆盖和跨任务泄漏。
- 各业务 Skill 自行创建目录并只约定文件名：拒绝，因为路径校验、生命周期、快照哈希、清理确认和审计状态会产生不兼容实现。

## Consequences

- 调用方必须先通过本地构件能力创建或登记任务，才能调用需要本地快照或产物的 Provider 操作。
- `task_ref` 是稳定逻辑引用而不是秘密或授权；即使它按 ADR 0030 进入飞书短期令牌租约，Provider Profile、操作权限和正式写入授权仍分别校验。
- `packages/local-artifacts/` 必须提供版本化公共接口或进程合同，但继续保持无 MCP、无网络、无外部凭证。
- 任务布局升级必须遵守公共契约 SemVer；旧任务只能通过显式迁移或兼容读取处理，不得静默改写 manifest。
