---
status: accepted
date: 2026-08-19
---

# 保留纯本地 Skill 并新增统一入口

`qa-case-xlsx-local` 保留现有名称、纯本地来源和纯本地交付边界，不增加飞书权限、公共 Provider 依赖或到其他 Skill 的静默转发；另建统一业务 Skill `qa-case-xlsx-unified`，复用现有本地生成核心并按需消费飞书 Provider，负责自动识别来源及请求用户选择交付通道。QAWORK 原版继续独占 `qa-case-xlsx` 身份；这个决定以增加一个明确入口换取现有调用的兼容性、权限可预期性和跨工作空间无歧义发现。

## Considered Options

- 直接扩展 `qa-case-xlsx-local`：拒绝，因为名称、测试和既有调用都承诺无飞书与无网络依赖。
- 将新入口命名为 `qa-case-xlsx`：拒绝，因为该名称属于 QAWORK 原版并已存在真实发现冲突。

## Consequences

- `qa-case-xlsx-unified` 如进入实现，必须使用独立的 `skills/qa-case-xlsx-unified/` 目录；当前迁移不创建该目录或入口。
- 只有形成第二个真实消费者、稳定合同和独立测试后，才另行把可复用生成核心抽取到 `packages/<package-name>/`；统一入口不得跨目录导入 `qa-case-xlsx-local` 的私有源码。
- 统一入口只通过已安装 Workspace Feishu Plugin 的公开 MCP 合同读取飞书；即使 Provider 位于同一仓库，也不得导入其私有源码、运行时、凭证或本机路径。
- `qa-case-xlsx-local` 的目录与 Git 归属迁移由 QA Skill Hub ADR-0035 授权并保留完整历史；本 ADR 仍只决定两个入口的身份和权限边界。
