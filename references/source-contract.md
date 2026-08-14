# 本地策划案源包契约

## 输入范围

一次任务可以包含一个或多个本地文件。支持：

- Word：`.docx`
- PDF：`.pdf`
- Excel：`.xlsx`
- 文本：`.md`、`.txt`
- 图片：`.png`、`.jpg`、`.jpeg`

不支持目录递归猜测、云盘链接、飞书链接、网页链接或仅给出标题而没有文件的输入。外链只能登记，不能访问。

## 来源身份

每个文件分配稳定 `source_id`：`SRC-001`、`SRC-002`……，并记录：

- 绝对路径、文件名、扩展名、字节数、SHA-256；
- `readable`、`partial`、`unreadable` 或 `excluded` 状态；
- 文本块、表格、Sheet/单元格、页码和图片证据定位；
- 排除项、外链、警告和视觉复核要求。

不得因为文件同名、标题相同或内容相似而合并来源身份。

## 定位格式

- DOCX 段落：`SRC-001#paragraph-12`
- DOCX 表格：`SRC-001#table-2-row-4`
- PDF 页：`SRC-002#page-7`
- XLSX 单元格：`SRC-003#sheet-功能配置!B12`
- 嵌入图片：`SRC-001#embedded-image-3`
- 独立图片：`SRC-004#image`

`source_ref` 必须能在 `source_packet.json` 或 `source_evidence_ledger.json` 中找到。

## 全量读取口径

- DOCX：读取正文、表格、页眉页脚可见文字和嵌入图片；逐页渲染复核文本框、浮动对象和排版信息。
- PDF：读取每页文字并逐页渲染；扫描页或文字抽取异常时以视觉结果为准，状态为 `partial` 直至复核完成。
- XLSX：读取所有可见且未排除 Sheet 的已使用区域、批注/超链接提示和嵌入图片；保留 Sheet 与单元格定位。
- 图片：逐张视觉读取，记录可辨识文字、状态、布局关系和无法辨识部分。
- Markdown/TXT：读取完整文件，保留标题或行号定位。

自动提取不是“已全读”的充分条件。`visual_review_required=true` 的证据必须由模型实际查看后写回 `review_status=reviewed`。

## 默认排除

- hidden 或 veryHidden Sheet；
- Sheet 名称含 `CP` 或 `反馈`；
- 临时锁文件，如 `~$*.xlsx`；
- 策划案内的外部链接目标。

排除项必须记录名称、原因和来源。用户明确要求纳入特定 Sheet 时，使用脚本参数覆盖默认排除，并在审计记录该决策。

## 事实层

`source_facts.json` 中每条事实包含：

- `fact_id`：`FACT-0001` 起连续编号；
- `topic`：稳定主题，例如“入口开放条件”；
- `statement`：人类可读的事实陈述；
- `status`：`confirmed` 或 `pending`；
- `source_refs`：至少一个定位；
- `project_scope`：可选，只有来源能证明时填写；
- `notes`：只记录歧义或限制，不写推测。

`count_policy.mode` 默认必须为 `natural`。只有用户明确要求固定数量、抽样或精简时，才改为 `user_fixed`、`sample` 或 `condensed`，并记录 `request_ref`；`user_fixed` 还必须记录正整数 `requested_count`。

若同一主题存在互斥值：

1. 不按“最新修改时间”“最后一个文件”或“看起来合理”选边；
2. 原始事实分别保留；
3. 增加 `conflicts[]`，列出各值和来源；
4. 相关事实与完整性项标记 `pending`；
5. 交付模式固定为草稿，等待用户决定。

## 可生成性

允许生成工作簿至少需要：

- 一个 `confirmed` 业务事实；
- 一个能从已确认事实建立的原子测试目标；
- 最终用例不依赖未读内容才能成立。

只有图片但尚未视觉读取、只有外链、只有“复用旧玩法”或全部事实冲突时，均不满足可生成性。
