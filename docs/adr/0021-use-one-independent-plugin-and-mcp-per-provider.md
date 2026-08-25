---
status: accepted
date: 2026-08-19
---

# 每个外部 Provider 使用独立 Plugin 和 MCP Server

每个外部系统能力 Provider 作为一个可独立安装、启用、升级和撤销的 Codex Plugin 发布，并拥有自己的 MCP Server。首个飞书 Provider Plugin 包含飞书受控语义工具及必要的授权、读取和交付指导 Skills，但不得调用、包装、导入或依赖 `lg-feishu` 的 MCP、Skills、运行时、凭证、配置和私有 registry。未来 SVN Provider 采用另一个独立 Plugin/MCP，并拥有不同的身份、Profile、权限、状态、工具命名和发布生命周期。

业务 Skill 通过公共契约直接调用目标 Provider MCP 暴露的命名语义工具。能力平台只提供契约、能力目录、成员发现、任务引用、兼容预检和测试套件，不建设转发所有 Provider 请求的中央 Broker MCP，也不允许一个 Provider 代另一个 Provider 执行远端操作。`packages/local-artifacts/` 继续以无网络的公共包或进程合同提供本地任务构件能力，不注册为 MCP Server。

Provider Plugin 内的 Skills 只负责说明何时使用该 Provider、如何完成 Profile 授权和如何解释结构化结果；具体 QA 用例、报告、配置查询等业务编排仍属于各业务 Skill。Provider MCP 独立拥有 transport、OAuth、分页、速率限制、物理资源定位、恢复状态与语义回读，业务脚本不得复制这些实现或直接调用远端 OpenAPI。

## Considered Options

- 建设一个覆盖飞书、SVN 和后续系统的公共 Broker MCP：拒绝，因为它会重新合并身份、权限、版本、故障和发布边界，并成为所有业务 Skill 的单点依赖。
- 只发布 SDK，由每个业务 Skill 直接调用外部 API：拒绝，因为鉴权、分页、错误语义、恢复和审计会散落到每个消费者。
- 把飞书与 SVN 放进一个 Plugin 但使用不同工具前缀：拒绝，因为工具命名隔离不能替代安装、凭证、Profile、故障和发布隔离。
- 将本地构件能力也包装成 MCP：拒绝，因为本地无网络文件操作不需要外部身份或独立服务运行时。

## Consequences

- 单个 Provider 不可用时，只阻断声明依赖该 Provider 的任务；其他 Provider 和纯本地业务 Skill 可以继续运行。
- 每个 Provider Plugin 必须单独声明版本、公共契约兼容范围、语义工具、授权方式和代表性只读健康检查。
- 业务 Skill 不依赖 Provider 私有代码或工具安装路径，只依赖公开工具 schema、结果 envelope 和能力声明。
- 跨 Provider 工作流由业务 Skill 编排，并分别完成各 Provider 的任务预检和身份绑定；不存在一次授权同时覆盖多个外部系统。
