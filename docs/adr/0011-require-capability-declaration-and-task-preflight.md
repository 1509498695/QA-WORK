---
status: accepted
date: 2026-08-19
---

# 使用 Provider 前强制能力声明与任务预检

每个外部系统 Provider 必须提供无需业务写入授权的只读能力声明和任务预检：能力声明返回稳定 Provider 身份、Provider 版本、支持的公共契约版本、资源类型和受控语义操作；任务预检针对业务 Skill 声明的本次需求，检查当前任务实际可调用的工具、配置、用户主体、客户端 Profile 绑定语义能力、Scope、资源类型和目标访问条件。业务 Skill 在首次调用及关键身份或目标变化后必须重新验证，不能依据目录存在、Plugin 已安装、MCP 已登记或历史调用成功推断本次可用。

## Considered Options

- 只检查 Plugin 安装和启用状态：拒绝，因为安装不证明当前任务已暴露工具、当前用户已授权或契约版本兼容。
- 业务 Skill 直接尝试正式操作并从错误推断能力：拒绝，因为能力不兼容、授权不足和目标问题会混成一次可能产生副作用的失败。
- 由每个业务 Skill 自定义就绪检查：拒绝，因为相同 Provider 会出现互不一致的身份、Scope 和版本判断。

## Consequences

- 未发现实际能力时返回 `capability_unavailable`，版本不兼容时返回 `contract_incompatible`，客户端绑定缺少所需语义能力时返回 `binding_capability_required`，外部 Scope 不足时返回 `auth_required`；这些状态不得触发原始 API 或其他 Provider 降级。
- 预检只能读取就绪状态和必要元数据，不得创建、复制或修改远端对象。
- 纯本地任务不因未安装外部 Provider 而失败；只有业务 Skill 声明需要相应外部能力时才执行其预检。
