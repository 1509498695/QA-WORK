---
status: accepted
date: 2026-08-25
---

# 公共能力与业务 Skill 采用联邦式多仓工作空间

能力工作空间是跨仓库的逻辑边界，不要求把公共 Provider 与业务 Skill 放进同一 Git 仓库。`D:\project\work` 持有公共能力契约、Feishu Provider、Provider Plugin 和后续 Provider 中立基础设施；业务 Skill 继续由各自业务仓库持有。`qa-case-xlsx-unified` 的首个实现留在 `D:\project\qa-case-xlsx`，在仓库内复用现有用例生成核心，并且只通过已安装 Plugin 的公开 MCP 合同消费 Workspace Feishu，不跨仓导入 Provider 私有源码或运行时。

只有在业务核心已经形成稳定、可独立版本化的包，并且迁移方案能够同时保留 Skill 身份、历史与兼容测试时，才另行决定是否移动业务 Skill。目录邻近、共同父目录或本机源码路径都不构成公共能力成员关系；跨仓身份和任务边界仍由版本化清单及本机登记显式建立。

## Considered Options

- 立即执行 ADR-0002 的单仓迁移：拒绝，因为 `D:\project\work` 尚未成为 Git 仓库，现有业务核心及未提交设计位于 `D:\project\qa-case-xlsx`，此时迁移会把业务入口设计与代码搬迁绑成一次高风险变更。
- 在 `D:\project\work` 新建薄 Skill 并跨目录导入用例仓库源码：拒绝，因为安装、移动或跨机器运行后物理路径会失效，也会绕过公开契约与版本兼容门禁。
- 复制用例生成核心到两个仓库：拒绝，因为规则、Schema、校验和视觉交付实现会立即产生双份真源。

## Consequences

- ADR-0002 被本 ADR 取代；`D:\project\work` 是否初始化为独立 Git 仓库作为单独实施步骤处理，本决定不授权移动或删除任何现有文件。
- 业务仓库不得依赖 `D:\project\work` 的源码路径、根虚拟环境或插件缓存路径，只能依赖公开 Provider 身份、工具 Schema、合同版本和结构化结果。
- 跨仓任务快照或本地构件共享开始前，必须先实现并验证成员清单、本机登记和任务边界；仅使用纯远端内存读取时不得伪称这些本地合同已经落地。
- 将来迁移业务 Skill 时必须单独提出计划，保留纯本地入口，并证明统一入口、Provider Plugin 与本地生成核心的兼容性。
