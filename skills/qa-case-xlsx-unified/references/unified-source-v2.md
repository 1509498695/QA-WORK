# 飞书统一来源 v2

## 当前范围

当前纵切接受用户明确给出的飞书 Docx、Wiki 或 Sheets 来源。每份来源保持独立身份；不因标题、URL 指向同一工作簿或内容相似而合并。纯本地与混合来源保留在统一入口设计中，但本纵切不得声称已经实现。

## 运行与来源身份

- `task_ref`：`qcxu_<UUIDv4>`，不含标题、账号、URL、Token 或正文。
- `source_id`：按用户声明顺序分配 `SRC-001`、`SRC-002`……；重新排序会产生不同源包哈希。
- `origin`：固定为 `feishu`。
- `locator`：保留用户明确提供的原定位符和 Provider 返回的 canonical URL。
- `resource_identity`：保存公开合同中的真实对象类型、资源 ID、工作簿 token、Sheet ID 和修订；不存在的字段省略，不以空字符串伪造。

## 双层来源记录

每份来源先形成不可变 `receipt`，再形成 Provider 中立 `normalized_source`：

```text
tasks/<task_ref>/
├─ manifest.json
├─ sources/receipts/SRC-001.json
├─ normalized/SRC-001.json
├─ assets/<sha256>.<ext>
├─ audit/
└─ delivery/
```

`receipt` 只白名单保存 Provider ID/版本、操作、状态、公开目标身份、修订、读取范围、内容哈希、完整性状态和安全警告。不得保存 access/refresh token、OAuth code/state、Cookie、授权头、Profile 明文身份、底层请求响应或未筛选错误正文。

`normalized_source` 必须引用 receipt 的 SHA-256，并把来源表示为有序内容单元：

- Docx：标题、段落、表格行、媒体占位及 Provider block 定位；
- Sheets：工作表身份、返回范围和单元格矩阵，保留 Sheet/单元格定位；
- 图片/附件：正文只保存内容哈希、媒体类型、大小、来源定位和视觉复核状态，二进制独立保存。

## 定位格式

- Docx Block：`SRC-001#block-<block_id>`
- Docx 表格：`SRC-001#block-<block_id>-row-<n>`
- Sheet 单元格：`SRC-002#sheet-<sheet_id>!B12`
- Sheet 范围：`SRC-002#sheet-<sheet_id>!A1:J40`
- 视觉资产：`SRC-001#asset-<sha256>`

每个 `source_ref` 必须能在 receipt、normalized source 或资产记录中找到。

## 完整性映射

| Provider 结果 | 来源状态 | 是否可正式生成 |
|---|---|---|
| `status=ok` 且所有层级 `retrieval_complete=true` | `complete` | 是 |
| `retrieval_incomplete` 或任一工作表/资产未完整 | `incomplete` | 否 |
| `auth_required` / `profile_selection_required` | `blocked` | 否 |
| 类型不支持、资源不存在或身份冲突 | `failed` | 否 |

不把远端路径存在、自动提取成功或部分正文返回当作完整来源。视觉内容只有在实际查看并记录观察后才从 `pending` 变为 `reviewed`。

## 哈希与回读

- 所有 JSON 使用 UTF-8、键排序、无多余空白的 canonical JSON 计算 SHA-256。
- 单一来源内容哈希覆盖 receipt 哈希、规范化内容和资产哈希列表。
- 源包哈希覆盖 Schema 版本和按用户声明顺序排列的来源身份/内容哈希。
- 每次写入本地暂存后重新读取并复算；远端内容或 revision 变化时创建新版本，不覆盖旧 receipt。
