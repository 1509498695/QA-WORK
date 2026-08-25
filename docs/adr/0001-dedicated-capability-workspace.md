---
status: accepted
date: 2026-08-19
---

# 在独立能力工作空间继续公共架构设计

以 `D:\project\work` 作为公共能力架构、隔离 Provider 和未来业务 Skill 的共同工作空间，各成员可以位于不同目录；`D:\project\qa-case-xlsx` 暂时保留为现有用例 Skill 的实现与迁移来源，不在其中继续承载跨业务公共 Provider。这个选择让公共契约从单一用例业务中独立出来，同时允许后续在明确迁移方案后把现有 Skill 纳入同一工作空间；工作空间最终采用单一 Git 仓库还是多个独立仓库，作为后续决策处理。

## Considered Options

- 继续在 `qa-case-xlsx` 仓库内设计全部公共能力：拒绝，因为飞书、SVN 和未来业务 Skill 的生命周期不属于纯本地用例系统。
- 让每个 Skill 在互不相关的目录中自行寻找 Provider：拒绝，因为公共契约、发现规则和兼容性门禁会再次分叉。
