---
name: qa-case-xlsx-local
description: 将用户上传的本地策划案或需求文件独立转换为陈镇个人风格的本地 Excel 测试用例。仅读取本地 DOCX、PDF、XLSX、Markdown、TXT、PNG/JPG，保留逐文件证据与图片映射，输出固定 A:J 用例表和本地审计包；不读取或写入飞书，不依赖 qa-case、QAWORK、Jira、Code Ask 或网络。
---

# qa-case-xlsx-local

把本地策划案源包转换成可复核的本地 `.xlsx` 测试用例。当前 Skill 是完整入口，不得转调 `qa-case` 或任何 QAWORK 私有流程。

## 不可越过的边界

- 只读取用户本次上传或明确指定的本地文件。支持 `.docx`、`.pdf`、`.xlsx`、`.md`、`.txt`、`.png`、`.jpg`、`.jpeg`。
- 不打开策划案中的外部链接；只记录链接文字、地址和所在位置。
- 不读取或写入飞书，不调用 Jira、Code Ask、MasterGo、KB、`qa-*`、QAWORK 命令或项目私有 MCP。
- 不从规则补造业务事实。规则只补充测试维度、拆分方式和表达风格。
- 无可读业务事实时不得生成工作簿。
- 任一来源未读完、来源冲突未解决、关键事实为 `pending` 或完整性矩阵仍有 `pending` 时，只能生成 `*-测试用例-待确认草稿.xlsx`。
- 只有全部正式门禁通过且 `pending=0` 时，才能生成 `*-测试用例.xlsx`。

## 运行前

1. 完整读取本文件。
2. 调用工作区依赖加载器，使用它返回的 Python、Node、Node packages、Git 与 PDF 工具路径；不得猜测或使用 QAWORK 的运行时。
3. 按源文件类型读取对应内置规范：DOCX 使用 documents，PDF 使用 pdf，XLSX 使用 spreadsheets，图片使用视觉读取。
4. 读取 [source-contract.md](references/source-contract.md)、[generation-blueprint.md](references/generation-blueprint.md)、[humanization.md](references/humanization.md) 和 [local-runtime.md](references/local-runtime.md)。
5. 需要项目化规则时，再按 [project-router.md](references/project-router.md) 只读取一个匹配的项目参考；无法确定项目就使用通用规则，不追问也不猜项目专属事实。

## 默认输出

输出到当前工作目录：

```text
qa-case-xlsx-output/<需求名>-<YYYYMMDD-HHmmss>/
├─ <需求名>-测试用例.xlsx
└─ audit/
   ├─ source_packet.json
   ├─ source_evidence_ledger.json
   ├─ source_facts.json
   ├─ generation_blueprint.json
   ├─ completeness_matrix.json
   ├─ pending_boundary_confirmations.json
   ├─ base_cases.json
   ├─ classification.json
   ├─ candidate_cases.json
   ├─ horizontal_rule_evaluation.json
   ├─ project_rule_evaluation.json
   ├─ final_cases.json
   ├─ case_mapping_ledger.json
   ├─ pipeline_validation.json
   ├─ delivery_readiness.json
   ├─ workbook_readback.json
   ├─ workbook-preview-header.png
   ├─ workbook-preview.png
   └─ evidence/
```

草稿只改变根目录工作簿文件名；审计结构保持一致。不要把中间 JSON 放到工作簿根目录。

## 标准流程

### 1. 固化源包

- 将一个或多个本地文件作为同一次源包处理，不合并或覆盖来源身份。
- 运行 `scripts/build_source_packet.py`，生成 `source_packet.json`、`source_evidence_ledger.json` 和 `evidence/`。
- DOCX/PDF 必须逐页渲染复核；XLSX 必须逐个可见且未排除的 Sheet 复核；图片必须逐张查看。
- 默认排除隐藏 Sheet，以及名称含 `CP` 或 `反馈` 的 Sheet，并把排除项写入源包。用户明确要求时才通过参数纳入。
- 对脚本标记为 `visual_review_required` 的图片、页面或嵌入对象完成视觉读取，并在证据账本写回 `review_status=reviewed`、`observations` 和证据定位。

### 2. 建立事实层

- 按 `source_facts.schema.json` 生成 `source_facts.json`。每条事实至少包含稳定 ID、事实陈述、状态和一个 `source_ref`。
- 同一主题在不同文件中出现互斥值时，两边都保留并新增 `conflicts[]`；状态保持 `pending`，绝不按文件顺序自动覆盖。
- 图片中看见的文字或状态必须引用证据账本中的图片定位，不能只写“见截图”。
- 只写来源能证明的内容。无法确认的旧玩法、配置值、入口、时序或结算规则进入 `pending_items`。

### 3. 生成通用蓝图

- 按 `generation_blueprint.schema.json` 生成 `generation_blueprint.json`，把需求拆成业务目标、角色、入口/资格、主流程、状态、数据边界、异常恢复、关联回归和来源定位。
- 需求只说“复用/沿用旧玩法”但未描述旧行为时，写入“旧玩法基线缺口”，不得生成臆测的回归用例。
- 按 GR-01～GR-08 生成 `completeness_matrix.json`。每项只能为 `covered`、`not_applicable` 或 `pending`；`not_applicable` 必须给出具体理由。
- 按 [pending-boundary-confirmations.schema.json](references/schemas/pending-boundary-confirmations.schema.json) 始终生成 `pending_boundary_confirmations.json`。没有待确认边界时写 `status=clear` 和空 `items`；有来源缺口、冲突或未定实现边界时，每项必须写稳定 `boundary_id`、模块、可直接回答的问题、推荐口径和 `source_refs`。
- 待确认边界清单只进入审计包和最终交付说明，不新增 Excel Sheet，也不混入 A:J 执行用例。

### 4. 套用本地规则并写候选

- 规则优先级：用户本次明确说明 > 当前源包事实 > 内置项目规则 > 50 条个人正式规则。
- 从 `references/rules/rule-index.json` 只加载蓝图实际触发的业务、对象、横向、风格和项目规则，禁止把所有规则做笛卡尔积。
- 先写不可变 `base_cases.json`，再写分类、候选、横向/项目规则评估、最终用例和映射账本。
- 状态、权限、对象、时序、正反路径改变主要验证目标时拆分；同一静态展示目标可以合并。
- 最终用例使用固定 A:J 十列：`用例编号、一级模块、二级模块、检查点、前置条件、操作步骤、预期结果、优先级、测试结果、备注`。
- 按业务阅读顺序排列用例，使同一一级模块、同一一级模块内的同一二级模块尽量连续；只有相邻同名组才能在工作簿中合并，禁止跨组全局合并。
- 默认生成自然完整数量，不预设条数。只有用户明确要求固定数、精简版或示例版时才压缩。
- `source_facts.count_policy` 默认写 `natural`；固定、抽样或精简模式必须记录用户要求来源，禁止在蓝图、脚本或产物中暗藏目标数量/分组配额。

### 5. 确定性校验

- 运行 `scripts/run_case_pipeline.py validate-rules`，规则数必须恰好为 50，索引和发布清单哈希必须一致。
- 运行 `validate-run`，验证跨产物 run/input/rule 版本、语义签名、基线去向、规则评估、原子拆分、模块命名和映射完整性。
- 运行 `readiness-local`，纳入源包、事实、蓝图、完整性矩阵和待确认边界清单门禁并计算正式/草稿文件名。
- 校验失败时只修对应产物，不跳过门禁，也不直接写工作簿。

### 6. 生成并回读本地工作簿

- 使用 `assets/local-case-template.xlsx` 和 `scripts/build_local_case_workbook.mjs` 生成工作簿；工作簿创建/编辑必须使用 Codex bundled `@oai/artifact-tool`。
- 按 [local-runtime.md](references/local-runtime.md) 在输出目录创建临时 `node_modules` junction，复制构建器后运行；完成后可移除该临时目录。
- 使用唯一的蓝色双层标题版式：A:B 为 DRI，C2:G5 为用例总数，C6:G8 为本地来源文件名，H:J 为结果统计，A9:J10 为两层用例表头；正式与草稿使用同一版式。
- 用例从第 11 行开始写入。只合并连续相同的 B 列一级模块与同一一级模块内连续相同的 C 列二级模块；D:J 正文不得合并。
- 不创建 Excel Table、不叠加筛选表主题，也不在工作簿顶部展示 SHA、项目码、规则版本、交付模式或待确认数量；这些信息只保留在审计包。
- 保留冻结窗格、优先级/测试结果校验、结果统计公式、自动换行与条件格式。
- `操作步骤` 每个动作一行并连续编号；不要把真实换行写成字面量 `\\n`。
- 构建器必须生成 `workbook_readback.json`、`workbook-preview-header.png` 与 `workbook-preview.png`。回读必须逐格核对 A:J 值、双层表头顺序、用例数、公式、顶部合并、模块合并、D:J 无合并、零 Excel Table、本地来源名和输出文件名；两张预览须无截断、遮挡和不可读文本。

## 交付门禁

只有以下条件同时成立才可结束：

- 所有用户提供的源文件均在 `source_packet.json` 中，且 SHA-256 可回查。
- 所有可见内容已读；明确排除项、未读项、外链和视觉证据均有记录。
- 事实、蓝图、完整性矩阵、候选、最终用例和映射账本之间可追溯。
- 50 条规则包、流水线和本地 readiness 全部校验通过。
- 工作簿根文件存在，文件名与 readiness 一致；A:J 精确回读一致。
- `pending_boundary_confirmations.json` 与 readiness 数量一致；有待确认项时交付说明逐项列出问题、推荐口径和证据定位。
- 预览已视觉检查；工作簿不含飞书 URL、QAWORK 路径、外部数据连接或宏。
- 交付时只引用根目录工作簿；审计目录用于复核，不把其内容冒充正式用例。

## 失败处理

- 文件损坏或格式不支持：保留源文件记录，标记 `unreadable`，转草稿；若没有任何已确认事实则停止，不生成工作簿。
- 视觉内容无法读清：标记 `partial` 和 `pending`，不得猜测。
- 来源冲突：列出冲突值和各自定位，转草稿，等待用户决定。
- 模板、规则包、回读或渲染失败：停止交付并报告具体门禁；不得用 CSV、飞书表或无模板 XLSX 替代。
