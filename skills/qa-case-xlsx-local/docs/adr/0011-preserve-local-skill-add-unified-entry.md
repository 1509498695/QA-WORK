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

- `qa-case-xlsx-unified` 的首个实现由本仓库持有，在本仓库内复用用例生成核心；`qa-case-xlsx-local` 仍保持独立入口和纯本地合同。
- 统一入口只通过已安装 Workspace Feishu Plugin 的公开 MCP 合同读取飞书，不跨仓导入 `D:\project\work` 的 Provider 私有源码、运行时或本机路径。
- 将业务 Skill 迁入其他仓库必须另行确认并提供身份、历史和兼容测试保留方案；本 ADR 不授权复制或移动现有实现。
