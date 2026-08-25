---
status: accepted
date: 2026-08-19
---

# 公共契约与各成员独立语义化版本

`contracts/*`、每个 Provider 和每个业务 Skill 分别采用独立语义化版本，不使用 monorepo 总版本推导兼容性；业务 Skill 必须声明所需能力及契约兼容范围，Provider 的能力声明返回其实际支持的精确版本或版本集合。向后兼容的能力新增使用 minor，缺陷修复使用 patch，破坏字段或语义的变化必须发布新 major；Provider 可以同时支持多个 major，但不得在 major 不匹配时静默转换或降级。

## Considered Options

- 整个 monorepo 使用一个锁步版本：拒绝，因为无关 Provider 和业务 Skill 会被迫一起发布，削弱已经确认的独立发布边界。
- 消费者始终使用仓库最新契约：拒绝，因为本地源码位置不能证明已安装 Provider 的运行时版本或兼容性。
- 破坏性变化直接覆盖旧契约：拒绝，因为尚未迁移的业务 Skill 会在无明确失败的情况下改变行为。

## Consequences

- `contract_incompatible` 必须报告需求范围与 Provider 实际版本，不得只返回笼统不可用。
- CI 必须对已声明的 Provider、契约和业务 Skill 组合执行兼容矩阵与契约测试；同仓源码导入不能替代已发布边界测试。
- 删除一个仍被声明支持的 major 前，必须先迁移所有已登记消费者并证明其预检与契约测试通过。
