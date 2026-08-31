# 飞书用例读写首个纵切

## 状态

2026-08-28 已开始实现。Skill 身份、来源/生成/交付合同、A:J 规范构建器、离线测试和个人发现 Junction 已完成；真实飞书来源读取、新建 Sheet 交付和同对象回读待验证。

## 目标

新增独立业务 Skill `qa-case-xlsx-unified`，在不改变 `qa-case-xlsx-local` 纯本地边界的前提下，完成一个真实的“飞书策划来源 → 可追溯最终用例 → Workspace Feishu 受管单 Sheet → 同对象业务回读”纵切。

## 用户体验

- 用户只提供策划来源链接和目标工作簿链接；若首次交付，给出或接受一个可见的新 Sheet 标题。
- Skill 自动路由 Wiki/Docx/Sheets、固化来源证据、生成自然数量用例并构建完整 Sheet 规范。
- 用户只在正式写入时处理 Provider 的一次 MCP 卡片，不复制 `registration_ref`、`operation_ref` 或规范哈希。
- 完成回复区分来源完整、用例生成、Provider 交付、业务回读、提交与推送。

## 本次包含

- 独立 `skills/qa-case-xlsx-unified/` Skill 和个人 Junction。
- 飞书来源 v2 回执/规范化语义、完整性状态与安全持久化边界。
- 复用 `qa-case-xlsx-local` 公开规则资源和校验 CLI，不导入其私有 Python 模块。
- `final_cases.json` 到 `workspace-feishu/sheet-delivery/v1` 的确定性 A:J 适配器。
- 初次 `create_new_sheet` 交付、Provider API/XLSX 双读和业务逐格回读。
- 离线契约测试、Skill validator、旧本地 Skill 回归和仓库总验证。

## 本次不包含

- 修改或弱化 `qa-case-xlsx-local` 的无网络边界。
- 本地/飞书混合来源、本地交付通道自动切换或飞书待确认草稿交付。
- 抽取新的共享 Python 包、导入 Provider 私有源码或复制凭据。
- 跨任务自动找回历史 `base_spec`、多人中央部署或正式 Marketplace 发布。

## 写入测试边界

- 真实来源和目标使用用户已经明确放入本次任务范围的飞书链接。
- 默认在目标工作簿中新建唯一命名验证 Sheet；不得覆盖来源 Sheet、`33K3ra` 或其他现有 Sheet。
- 预览前规范和标题固定；卡片拒绝/取消即停止。
- 部分写入只按原操作恢复；不得重新预览、切换目标或创建第二张替代 Sheet。

## 完成定义

- 新旧两个业务 Skill validator 与测试全部通过，发现 Junction 指向仓库规范源。
- 真实来源 `status=ok` 且 `retrieval_complete=true`，用例生成门禁无 pending。
- 新 Sheet 写入返回 `delivered`、`last_completed_step=export_verified`、`retrieval_complete=true`。
- 同一工作簿 token/Sheet ID 的 A:J 标题、表头和用例值逐格一致，来源与其他 Sheet 未改变。
- 设计文档明确当前纵切和未实现能力，没有提交或推送即不得声称已发布。
