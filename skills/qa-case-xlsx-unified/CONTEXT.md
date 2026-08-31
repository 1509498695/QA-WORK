# qa-case-xlsx-unified 领域语言

本上下文定义飞书策划来源生成 A:J 测试用例并受控交付到飞书 Sheets 的首个业务纵切。

## 系统边界

**统一用例入口**：
保留纯本地入口不变，独立编排飞书来源、用例生成门禁和飞书交付的业务 Skill。
_Avoid_：本地 Skill 在线模式、Workspace Feishu 业务插件、QAWORK 别名

**飞书来源**：
用户本次明确提供、经 Workspace Feishu 公开读取合同完整固化的 Docx、Wiki 或 Sheets 对象。
_Avoid_：飞书链接、历史文档、临时本地副本

**来源回执**：
安全白名单保存 Provider 公开身份、修订、范围、哈希和完整性状态的不可变记录。
_Avoid_：OpenAPI 响应、Token、日志、正文快照

**规范化来源**：
引用来源回执和资产哈希、供生成核心消费的 Provider 中立内容单元集合。
_Avoid_：Docx Block 返回、Sheet 下载文件、本地来源伪装

## 生成与交付

**飞书用例规范**：
由已校验 `final_cases.json` 确定性构造的 `workspace-feishu/sheet-delivery/v1` A:J 单 Sheet 完整目标状态。
_Avoid_：原子 API body、XLSX 上传、样式补丁

**写入目标**：
用户本次明确指定的飞书工作簿及经预览确定的新 Sheet 或内容空白 Sheet。
_Avoid_：来源对象、最近写入对象、历史登记

**业务同对象回读**：
Provider API/XLSX 双读完成后，再以返回的同一工作簿 token 和 Sheet ID 逐格核对 A:J 业务值。
_Avoid_：写入成功、工作簿可打开、抽样截图

**飞书用例已交付**：
生成门禁通过、Provider 返回 `delivered/retrieval_complete=true`，且业务同对象回读与不可变用例规范一致。
_Avoid_：预览完成、MCP 卡片已接受、测试通过

## 当前限制

**飞书读写纵切**：
当前只实现飞书来源到飞书新 Sheet/受管 Sheet 的路径，以真实交付验证统一入口合同。
_Avoid_：完整统一入口、混合来源、正式多人系统
