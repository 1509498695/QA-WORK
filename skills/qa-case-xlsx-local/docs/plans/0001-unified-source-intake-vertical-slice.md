# 统一来源接入首个纵切

## 状态

设计范围已确认，尚未开始实现。

## 目标

在未来独立的 `D:\project\work\skills\qa-case-xlsx-unified` 中建立首个可运行纵切：根据用户明确提供的定位符识别本地、飞书或混合来源，将各来源固化为可验证的 v2 `sources[]` 统一来源包，并在业务任务暂存区完成结构、哈希、修订、完整性和证据定位回读。当前目录迁移不创建该 Skill。

本纵切以“统一来源包已经形成并通过验证”为完成点，不生成测试用例，不创建正式 Excel，也不写入飞书。

## 已确认范围

- 保留 `qa-case-xlsx-local` 的名称、v1 `files[]` 输入合同、纯本地执行边界和现有行为。
- 新建独立的 `qa-case-xlsx-unified` 入口；只有共享核心另经设计、形成稳定合同并进入 `packages/` 后，才通过该公共包复用，不跨目录导入本地 Skill 私有源码。
- 只根据用户明确提供的本地路径或飞书 URL 识别来源；不跟随材料中的外链，不扫描磁盘猜测来源。
- 支持单一本地来源、单一飞书来源以及同一任务中的本地与飞书混合来源。
- 飞书读取只消费已安装 Workspace Feishu Plugin 的公开 MCP 合同，不导入 Provider 私有源码、运行时、凭证或缓存。
- 统一入口采用 v2 `sources[]`；本地 v1 `files[]` 通过单向适配进入统一来源语义，不把飞书快照伪装成本地文件。
- 每个来源采用双层保存：先固化不可变的来源回执，再生成 Provider 中立的规范化来源快照；快照必须引用回执哈希。
- 来源回执只保存经安全筛选并规范序列化的公开合同结果：本地来源保存 v1 来源记录及校验结果，飞书来源保存 Workspace Feishu MCP 的公开结构化结果；不得保存 Token、Secret、OAuth code/state、Provider 私有状态或未筛选错误正文。
- 后续事实提取和生成核心只消费规范化来源快照，不直接依赖 Docx Block、Sheets 返回字段或其他 Provider 专属结构。
- 图片和附件按内容哈希独立保存；来源回执和规范化来源快照只引用资产，不重复嵌入 Base64。
- 来源回执必须先完成落盘与哈希回读。规范化失败时保留回执，但该来源和统一来源包不得标记为完整；来源内容或修订变化时创建新快照版本，不覆盖旧版本。
- 来源结果写入业务任务暂存区并执行同对象回读；暂存产物不是正式交付物。
- 任一用户声明来源读取不完整、身份未确定、权限不足或验证失败时，统一来源包不得标记为完整。

## 已确认任务暂存边界

- 首个纵切由 `qa-case-xlsx-unified` 在本仓库内提供最小 `TaskStore`；不等待、复制或跨仓导入尚未实现的公共 `local-artifacts`。所有任务目录操作必须通过该内部接口完成，以便后续在不改变 v2 来源合同的前提下替换实现。
- 任务根由系统工作区配置解析为 `<system-workspace>/tasks/<task_ref>/`。未来统一入口的开发环境预期使用被 `.gitignore` 排除的 `D:\project\work\skills\qa-case-xlsx-unified\tasks/`；用户输入、来源内容和 Provider 返回值都不得决定物理任务路径。
- 统一入口在首次来源读取前生成一次 `qcxu_<UUIDv4>` 形式的 `task_ref`，并在整个任务中保持不变。该值不包含标题、账号、来源定位或秘密，不是文件路径、凭据或授权。
- `TaskStore` 必须以排他方式创建任务根并原子写入 `manifest.json` 后，才能调用本地适配器或 Workspace Feishu MCP。已存在的同名任务不得覆盖或静默复用。
- `manifest.json` 至少记录任务合同与 Schema 版本、`task_ref`、所有者 Workspace、所有者 Skill、创建时间、生命周期状态、用户声明来源，以及来源回执、规范化快照和资产的内容哈希引用；不得记录 Token、Secret、OAuth code/state 或 Provider 私有状态。
- 首个纵切使用以下任务分区；正式输出和交付预览目录留待后续纵切定义，不影响当前分区名称：

```text
<system-workspace>/tasks/<task_ref>/
├─ manifest.json
├─ sources/
│  └─ receipts/
├─ normalized/
├─ assets/
└─ audit/
```

- Provider 只接收逻辑 `task_ref` 并返回公开结构化结果；它不能接收、推导、遍历或写入物理任务目录。

## 非目标

- 测试事实提取、生成蓝图、规则展开、用例生成和工作簿构建。
- 本地正式交付、飞书受管对象创建或修订，以及任何正式写入授权。
- 修改 `qa-case-xlsx-local` 的公开 Skill 合同或把它变成统一入口的别名。
- 修改 Workspace Feishu Provider 的只读工具或增加飞书写入能力。
- 导入 `src/` 或 `plugins/workspace-feishu/` 中的 Provider 私有实现；统一入口只能消费已安装 Plugin 的公开 MCP 合同。

## 验收门槛

- 本地、Docx、Sheets、Wiki→Docx、Wiki→Sheets 和混合来源均有合同测试；远端自动化使用脱敏的公开结果夹具，不依赖真实业务内容。
- 每个来源具有稳定且不冲突的来源身份、真实来源类型、原始定位、内容哈希、完整性状态和可追溯证据。
- v1 本地适配前后文件身份、SHA-256、内容单元和证据引用保持一致。
- 飞书来源保留 Provider 身份、合同版本、解析后的真实对象类型、修订、内容哈希和完整性警告；不得持久化 Token、Secret、OAuth code/state 或未经白名单允许的远端错误正文。
- 统一来源包经过 Schema 校验、语义校验和落盘回读；重新读取暂存产物得到相同的来源集合与内容哈希。
- 未安装 Provider、授权缺失、多个 Profile 未选择、资源类型不支持和 `retrieval_incomplete` 均返回彼此可区分的失败或待处理状态，不静默降级为本地来源。
- 现有纯本地测试保持通过，新增统一入口测试不得要求网络或修改飞书。

## 尚未决策

1. Docx Block、媒体资产及 Sheets 工作表在统一快照中的规范化粒度。
2. 混合来源包的整体哈希和来源排序规则。
3. `retrieval_incomplete`、待授权、待选 Profile 与不可恢复失败如何映射到业务来源状态。
