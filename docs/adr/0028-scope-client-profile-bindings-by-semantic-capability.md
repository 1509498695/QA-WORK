---
status: accepted
date: 2026-08-19
---

# 客户端 Profile 绑定按语义能力最小授权

ADR 0027 定义的每个客户端 Profile 绑定必须携带用户明确批准的语义能力集合 `allowed_capabilities`。集合成员使用 Feishu Provider 能力声明中的稳定、版本化语义能力 ID，例如 `feishu.docx.read`、`feishu.sheets.read`、`feishu.docx.managed_write`，不能使用飞书原始 OAuth Scope、MCP 工具名、HTTP 路径或“全部能力”等宽泛值表达。创建绑定时只批准客户端本次明确请求且 Provider 实际声明的能力，不自动包含该 Profile 当前已有或以后新增的其他能力。

绑定能力集合是客户端使用 Profile 的授权上限，不是本次任务的默认执行清单。授权控制面按 ADR 0030 签发任务级短期访问令牌租约前，必须确认任务请求的每项语义能力都在当前绑定集合内，并由 Provider 将这些能力映射到 Profile 实际需要的最小飞书 OAuth Scope；OAuth Scope 已存在不能反向扩大绑定，绑定能力已批准也不能跳过缺失 Scope 的增量 OAuth。Provider 新增能力、能力 ID 变化或用户要扩大现有集合时，必须按 ADR 0029 展示客户端、Profile 和新增语义能力并再次取得该 Profile 用户确认，不能因 Provider 升级或 Scope 增加而自动继承。

写入语义能力只允许该客户端代表 Profile 进入对应的受控预览、确认和执行流程，不授权任何确定内容或目标的正式写入。每次正式写入仍须满足 ADR 0008 的单次内容绑定授权；没有写入语义能力的绑定不得进入写入授权流程。用户缩减能力集合或撤销绑定可以立即收紧后续租约，不要求先维持旧权限；已经交付到客户端内存的飞书短期令牌按其实际有效期和单独定义的应急策略处理。

## Considered Options

- 绑定自动继承 Profile 的全部当前和未来 Scope：拒绝，因为新增 OAuth Scope 会静默扩大所有既有设备的能力，用户无法判断哪个客户端获得了什么权限。
- 绑定只区分“只读”和“读写”：拒绝，因为 Docx、Wiki、Sheets、图片与附件的资源边界不同，宽泛分组会授权未请求的能力。
- 直接把飞书 OAuth Scope 保存为绑定权限：拒绝，因为平台 Scope 是 Provider 私有实现细节，可能覆盖多个语义操作，也会把业务授权耦合到外部 API 命名。
- 每次任务都重新授权全部语义能力：拒绝，因为设备与 Profile 的长期信任关系会退化为重复确认；任务级目标和内容风险已经由预检与正式写入授权单独处理。

## Consequences

- 客户端 Profile 绑定必须保存能力集合、能力合同版本、批准时间、批准用户主体和不可变审计证据；能力扩大形成新的绑定修订，不覆盖原批准证据。
- Provider 能力声明必须为每个稳定语义能力 ID 给出资源类型、读写性质、所需 Scope 映射和兼容合同版本；业务 Skill 只依赖语义能力 ID。
- 任务预检必须区分 `binding_capability_required` 与 `auth_required`：前者表示客户端绑定未获所需语义能力，后者表示绑定已允许但 Profile 缺少或失去对应 OAuth Scope。
- 新 Provider 版本不得把既有语义能力 ID 重新解释为更宽的资源或副作用；需要扩大权限语义时必须发布新能力 ID 或不兼容合同版本并重新确认。
- 能力集合的默认申请模板、固定有效期和已签发令牌的应急失效策略分别决策，不能由本 ADR 推断。
