# 飞书 A:J 用例交付合同

## Sheet 布局

默认模板登记在 [default-template.json](default-template.json)。每次生成先通过 Workspace Feishu Provider 只读刷新该定位符并保存模板回执；配置里的历史 revision/hash 只是漂移对照。模板对象永远只读，也不能作为策划来源或交付目标。

交付矩形固定从 A1 开始。默认标题格式为 `<需求名>用例-qa-case-xlsx-unified-<YYYYMMDD>`；用户明确指定标题时使用其原文：

- 第 1 行：本次需求标题，合并 `A1:J1`；
- 第 2–8 行：DRI、用例总数、策划来源/模板链接、P/F/D/N/A 统计、执行率与通过率；
- 第 9–10 行：双层 A:J 表头；
- 第 11 行起：按最终业务顺序写入用例，A 列使用 `=ROW()-10`；
- B/C 仅对连续相同的一级/二级模块分组做纵向合并，二级模块分组不能跨一级模块；D:J 永不因模块分组而合并；
- 冻结前十行；不创建 Excel Table、筛选主题、条件格式、数据验证、批注、图表或嵌入媒体。

用例总数与 P/F/D/N/A 统计公式必须按本次实际末行动态生成，不能复制模板中的固定末行。执行率与通过率引用统计区；用例为空时构建直接失败。

Provider v1 当前不支持 `wrap_text=true`，因此交付规范不声明自动换行。操作步骤保留真实换行，列宽和行高使用确定值；这是一项展示限制，不得通过原子 OpenAPI 补写未验证样式。

Provider v1 同样不声明模板中的下拉数据验证、单元格内局部富文本或百分比数字格式。交付保留等价的纯文本、颜色和公式，不能绕过 Provider 补写这些能力。

## 确定性构建

`scripts/build_feishu_case_spec.py` 只接受已经通过生成门禁的 `final_cases.json`。同一文件与标题必须产生逐字节等价的 canonical 规范哈希。

```powershell
& <bundled-python> <skill-root>\scripts\build_feishu_case_spec.py build `
  --final-cases <final_cases.json> `
  --title <title> `
  --source-url <source-url> `
  --out <sheet-spec.json>

& <bundled-python> <skill-root>\scripts\build_feishu_case_spec.py verify `
  --final-cases <final_cases.json> `
  --title <title> `
  --source-url <source-url> `
  --spec <sheet-spec.json>
```

多个来源重复传入 `--source-url`。`--template-url` 仅用于用户本次明确覆盖模板的情况，且 build/verify 必须使用同一值。

脚本拒绝空用例、非连续编号、非法优先级/结果码、未编号步骤、非法备注、超长文本、静态公式范围、模块合并后的语义漂移，以及超过 Provider 5,000 行或 200,000 单元格的交付。

## 首次交付

1. 用户给出目标工作簿和明确新 Sheet 标题。
2. 默认 `placement_mode=create_new_sheet`；带 `sheet=` 的 URL 只定位工作簿，原 Sheet 不变。
3. 预览返回 `preview_ready` 后，显示实际工作簿、目标标题、用例数、矩形、过期时间和 `preview_sha256`。
4. 应用必须原样使用预览的 `operation_ref`、`task_ref` 与完整规范，并由 MCP 卡片确认。
5. 只有 `delivered + retrieval_complete=true` 才进入业务回读。

## Wiki 父节点下新建工作簿

当用户给出 Wiki 目录/父节点链接并明确要求在其下新建表格文件时：

1. 使用 `placement_mode=create_new_workbook`，把精确 Wiki 父节点 URL 作为 `locator`；必须提交 `requested_workbook_title`，并省略 `requested_sheet_title`。
2. 预览必须完整分页读取直接子节点，确认文件名大小写不敏感唯一；父节点、子节点集合或规范变化后，旧预览失效，不能自动改名或换目录。
3. MCP 确认卡片同时授权创建一个新电子表格文件和写入其自动创建的唯一默认 Sheet。Provider 不改默认 Sheet 标题，并在创建后返回精确工作簿 token、Wiki 节点 Token/URL 与 Sheet ID。
4. 创建请求结果不明时，只使用原 `operation_ref + task_ref + spec` 对账；不得重新预览、创建替代工作簿、删除候选文件或按名称盲目接管。
5. 业务同对象回读必须使用 Provider 返回的新工作簿和 Sheet 身份；父节点读取、同名搜索或本地规范验证都不能代替新对象逐格回读。

## 业务同对象回读

Provider 的 API/XLSX 双读证明结构交付；业务 Skill 还要使用返回的精确 Sheet ID 进行 A:J 逐格比较：

- A1:J10 的标题、统计区、参考区、双层表头、公式和合并结构一致；
- 第 11 行起的用例行数一致；A 列编号公式允许远端以公式或等价计算值表示，B/C 合并单元格先向下语义展开，再逐个比较 A:J；
- 工作簿 token、可用时的 Wiki 节点 Token、Sheet ID、Sheet 标题和 revision 对应同一交付对象；
- 来源 Sheet、其他 Sheet 的身份和数量没有因交付被替换。

把 `spec_sha256`、Provider `operation_ref`、`registration_ref`、远端 revision、Provider evidence hash 和业务回读 hash 保存到审计结果。任何一层不完整都不能报告交付完成。

## 后续修订

只有调用方仍持有最近一次完整 `sheet-spec.json` 时才允许修订。解析精确链接后，使用上一规范作为 `base_spec`、按默认模板构造的新规范作为 `next_spec` 单次调用修订工具；不能新建替代工作簿或替代 Sheet。模板采用只改变版式、公式和允许的 B/C 模块合并，不得改变当前用例语义。同一不可变请求失败恢复时不重新确认、不切换目标、不重新生成规范。
