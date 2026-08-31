# Workspace Feishu 首个受管单 Sheet 修订纵切

## 状态

设计已于 2026-08-27 完成逐项确认，本地 `v0.6.0` 纵切现已实现并通过自动化验证：规范源、v2→v3 迁移、登记解析、一体化修订、双重验证、退役区域清理、MCP 合同、Plugin Skill、私有 runtime 和 cachebuster 已同步。2026-08-28 针对真实导出暴露的退役样式兼容问题补充了确定性中性样式和同操作恢复路径；仓库级完整验证、18 项本地 QA Skill 测试、runtime/source 一致性与 Plugin 清单校验均通过。

Marketplace 已安装 `0.6.0+codex.20260828025822`。真实目标固定为 `33K3ra / Workspace写入复测-20260827`：`no_change` 在版本 1 完整通过；同尺寸 A1 修订已交付版本 2，远端 revision 76 且 `retrieval_complete=true`；随后 6×5→5×4 缩表在远端 revision 95 完成 API 验证后，XLSX 导出发现退役单元格 A6 仍保留旧的垂直居中样式。修复后的新 runtime 使用原登记、原任务、原规范和原操作 `rev_1b2d5bc3ceac169a140915a96e3184d0` 定向中和退役样式并重新双读，最终在远端 revision 96 返回 `delivered`、`managed_version=3`、`last_completed_step=version_committed` 和 `retrieval_complete=true`。至此满足下文“Provider 同任务修订纵切已完成”的真实交付定义；跨任务自动找回基线与正式多人部署仍明确不在本次范围。

首个修订纵切限定为同一调用方任务仍持有最近一次完整规范的场景。Provider 和 Plugin 能力完成不等于跨任务业务集成完成；当前仓库不新增隐藏的全局正文缓存，不修改明确禁止飞书的 `qa-case-xlsx-local`，也不宣称用户在任意新任务中只凭链接即可找回历史基线。

## 用户可见目标

- 用户继续使用原飞书链接和同一个工作表，不新建替代 Sheet，不改变 Sheet ID。
- Skill 自动把链接唯一解析为既有受管登记；用户不复制或管理 `registration_ref`。
- 同一任务持有上一版规范并生成下一版规范后，只调用一次修订工具，只展示一次 MCP 确认。
- 行列增加、减少以及值、公式、样式、尺寸、冻结和合并变化都按完整目标状态修订；缩表会清除上一版退役区域，不删除网格行列。
- 无实际变化时只读验证并返回 `no_change`，不弹确认、不写入、不增加版本。

## 本次范围

### 包含

- 在首次交付成功结果中公开稳定 `registration_ref` 和 `managed_version=1`，并为现有登记安全回填版本元数据。
- 新增只读登记解析能力，把精确 Sheet/Wiki 链接唯一映射到当前 Profile 下的受管登记。
- 新增一体化 `feishu_managed_sheet_revise` 工具，输入 `registration_ref`、`task_ref`、`base_spec` 和 `next_spec`。
- 只接受当前 `workspace-feishu/sheet-delivery/v1`；两份规范必须通过相同 Schema 和现有安全上限。
- 修订前 API + 临时 XLSX 双重基线验证、确定性语义差异、一次 elicitation、检查点式前向写入和写后双重验证。
- 同一登记的追加式版本元数据、单活动修订锁、确定性幂等恢复和稳定诊断。
- Plugin manifest、私有 runtime、`feishu-write` Skill、README、测试与构建脚本同步。

### 不包含

- 跨任务自动找回或迁移调用方 `base_spec`，以及 `packages/local-artifacts/` 的首次实现。
- 修改或扩展 `qa-case-xlsx-local` 的本地文件独立边界。
- 任意非受管工作表覆盖、工作表重命名、隐藏/取消隐藏、物理删除行列、删除或放弃受管对象。
- 多 Sheet 原子修订、Docx 写入、Bitable、图表、评论、嵌入媒体或 Schema v2 迁移。
- `central-v1` 多人部署、跨设备登记同步或生产就绪声明。

## 公开合同

### 登记解析

新增只读工具 `feishu_managed_sheet_registration_resolve`：

- 输入：`locator`、稳定 `task_ref`、可选 `profile_ref`。
- 精确 Sheet 链接必须匹配同一 Profile、工作簿 token 与 Sheet ID 的一条登记。
- 未指定 Sheet 时，只在该工作簿恰好存在一条受管登记时自动选择。
- 零条匹配返回稳定的未登记状态；多条匹配返回目标不唯一，不按名称、索引或最近使用记录猜测。
- 成功结果返回 `registration_ref`、当前 `managed_version`、当前规范摘要以及刷新后的工作簿/工作表展示字段；它不产生写入授权。

### 一体化修订

新增 `feishu_managed_sheet_revise`：

- 必填：`registration_ref`、`task_ref`、`next_spec`。
- `base_spec` 允许缺省以返回稳定 `base_spec_required`；存在时必须完整符合 v1，并且摘要等于登记当前版本摘要。
- Provider 从登记加载固定 Profile 和受保护目标，不接受放置模式、工作表名称、任意 URL 或原子 API body 重新选择目标。
- 规范对身份由 `registration_ref + task_ref + base_spec_hash + next_spec_hash` 确定；同一身份重试恢复原操作，不要求调用方手工传递 `operation_ref`。
- `declined`、`cancelled` 或过期后再次尝试必须使用新的 `task_ref`，不能复活已经终结的授权。

成功或受控结果至少包含：

- `registration_ref`、审计用 `operation_ref`、`managed_version`；
- 当前目标展示信息、`base_spec_hash`、`next_spec_hash`、完整 `preview_sha256`；
- `status`、最后检查点、远端 revision、稳定诊断码；
- API/XLSX 证据、`retrieval_complete` 和内容证据哈希。

`revision_ready` 属于调用方业务结果，不由 Provider 猜测。调用方能够把已交付 `next_spec` 提升为下一基线时报告 `true`；持久化失败时保持 Provider `delivered`，另报 `baseline_persistence_incomplete`。

## 修订预检

1. 加载 `registration_ref`，绑定登记中的 Profile、工作簿 token 和 Sheet ID。
2. 验证没有其他未终结修订，并以当前版本摘要比较 `base_spec`。
3. 在线核验目标仍存在、可见、可访问且稳定身份未变。名称或排序变化只刷新展示字段；隐藏、删除、替换或主体不兼容均失败关闭。
4. 使用 API 回读验证值、公式、合并、冻结和矩形外业务空白。
5. 临时导出整簿 XLSX 到当前调用内存，只解析目标 Sheet，验证样式、行高和列宽；不写入磁盘或审计包。
6. 任一基线证据不完整时返回 `baseline_verification_incomplete`，不进入确认。
7. 摘要相同则返回 `no_change` 和当前版本，不建立写入操作。
8. 摘要不同时计算有界语义差异和修订退役区域，持久化不可变候选操作后展示一次 MCP elicitation。

## 唯一确认卡片

卡片只展示授权所需的有界信息：

- 当前账号主体、工作簿、工作表、链接和稳定登记；
- 受管版本 `N → N+1`；
- 旧新交付矩形；
- 新增、修改、清空单元格数量；
- 公式、合并、样式、尺寸和冻结变化计数；
- 退役区域精确范围与清理数量；
- 完整 `preview_sha256`、基线摘要和目标摘要。

不展开值或公式正文。业务内容审阅由上层 Skill 完成；Provider 卡片不能只显示模糊的“更新表格”。目标、规范或主体变化会使候选操作失效。

## 退役区域

所有交付矩形从 `A1` 起始。`base_rect - next_rect` 最多拆成不重叠的底部带和右侧带：

- 清除值、公式、链接、富文本及单元格样式；
- 飞书 `clean=true` 的成功响应不能单独证明导出样式已中和；退役区域还需显式写入确定性的无字体覆盖、左/顶对齐、无边框和无格式器状态，并以 XLSX 回读为准；
- 拆除任何进入退役区域的旧合并；
- 完全退役的行重置为 `24 px`，完全退役的列重置为 `100 px`；
- 不删除行列，不触碰 `base_rect` 之外的区域；
- 写后证明退役区域无业务内容、无跨界合并，并验证已执行的样式与尺寸清理。

## 前向写入与恢复

接受确认后按固定顺序执行：

1. `revision_reserved`：事务性锁定登记与候选版本。
2. `grid_extended`：只补足 `next_spec` 所需网格。
3. `base_merges_removed`：拆除全部基线合并，后续统一重建目标合并。
4. `next_values_written`：写入下一版完整矩形，包括显式空值。
5. `retired_values_cleared`：清空退役区域业务内容。
6. `union_styles_cleared`：清理目标矩形和退役区域的受管样式。
7. `next_base_style_written` 与 `next_style_ranges_written`：重建目标样式。
8. `dimensions_written`：写目标尺寸并重置完全退役轴。
9. `freeze_written`：写工作表级冻结设置。
10. `next_merges_written`：最后建立目标合并。
11. `api_verified` 与 `export_verified`：验证下一版完整状态和退役清理。
12. `version_committed`：原子追加交付版本并前移登记当前指针。

每一步持久化检查点。明确未产生副作用的失败可以停止；部分成功、超时或合同不明进入 `recovery_required`，API 已证实但 XLSX 证据不足进入 `verification_incomplete`。相同不可变请求自动回读现场并前向恢复，不回滚、不换目标、不新建版本、不重复确认，也不盲目重发不能证明未完成的请求。

## 本机状态迁移

操作状态库从 Schema v2 以只增不破坏方式升级：

- 保留现有 `operations`、授权、受管登记和三次真实交付证据，不删除、不重新加密、不改变 `registration_ref`。
- 为受管登记增加当前版本、当前交付证据及展示快照字段，并从既有已交付操作回填版本 1。
- 新增不可变 `managed_sheet_versions`，保存版本、父/目标摘要、操作、交付证据、远端 revision 与时间，不含正文。
- 新增修订操作和修订授权状态，避免把无放置模式的修订强塞入首次交付 `operations` 表。
- 通过事务、外键、唯一版本约束和每登记一个活动修订约束防止竞争；迁移失败必须回滚并保持 v2 可读，不能重建空库。

具体 DDL 在实现前先用旧 v2 fixture 做迁移测试，再落代码；数据库中的受保护目标继续使用现有 Windows DPAPI 合同。

## 实现切片

1. 扩展公共状态与错误合同：`no_change`、`base_spec_required`、`baseline_verification_incomplete`、版本及登记结果字段。
2. 为现有首次交付返回登记引用和版本，完成 v2→v3 无损迁移与回填。
3. 实现登记读取/解析、版本历史、活动修订锁、候选操作和单次授权。
4. 实现规范对差异、退役范围分解和有界确认摘要。
5. 扩展 Feishu gateway：基线双重验证、拆合并、退役清理、尺寸重置、修订写入和现场对账。
6. 实现一体化修订服务和 MCP elicitation；同一请求自动恢复。
7. 更新 Provider manifest、README、`feishu-write` Skill 与 Plugin 私有 runtime。
8. 完成隔离构建、Marketplace cachebuster 更新与重新安装，并在用户确认的同一真实 Sheet 上完成 `no_change`、同尺寸修订和缩表修订。

## 自动化验证门槛

- v2 数据库迁移保留既有操作、登记、DPAPI 载荷、交付哈希和版本 1 回填；损坏或重复数据失败关闭。
- 精确链接、Wiki 解析、无 selector 单登记、零/多登记以及 Profile 不匹配的解析测试。
- 基线缺失、摘要错配、API 漂移、样式/尺寸漂移、隐藏/删除/替换目标均保持零写入。
- `no_change` 完整双读且无 elicitation、无远端写请求、无版本增长。
- 增长、同尺寸修改、仅缩行、仅缩列、同时缩行列及一增一缩的退役区域测试。
- 旧合并拆除、新合并重建、跨退役边界合并、空值清除和中性尺寸验证。
- 每个检查点前后注入明确失败、超时、部分批次和不明响应，证明只读对账、前向恢复和不重复确认。
- 两个并发修订只能有一个候选版本；相同规范对重试复用原操作。
- 最终 API 与 XLSX 证据完整才 `delivered`，版本指针只前移一次；调用方基线保存状态与 Provider 结果分开测试。
- 根 Workspace、Plugin 私有 runtime、Skill 结构、边界扫描、编译、锁文件和 runtime/source 字节一致性全部通过。

## 完成定义

只有以下条件全部成立，才能报告“Provider 同任务修订纵切已完成”：

- 规范源实现、迁移、自动化、Plugin/Skill 文档和私有 runtime 全部同步验证；
- 现有 v0.5 首次交付及只读/授权能力无回归；
- 在用户重新确认的确定测试工作表上，至少完成一次同尺寸修订、一次缩表修订和一次 `no_change`，并对同一 Sheet ID 取得完整写前及写后证据；
- 真实操作均返回稳定 `registration_ref`、正确 `managed_version`、`status=delivered` 或 `no_change`，且 `retrieval_complete=true`；
- 未把跨任务基线自动找回、正式多人部署或任意非空 Sheet 覆盖表述为已完成。
