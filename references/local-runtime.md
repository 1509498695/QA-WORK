# 本地运行方式

## 原则

- 每次先调用工作区依赖加载器，使用返回的绝对路径。
- Python 只用于源文件提取、JSON 校验和测试；工作簿创建/编辑使用 bundled `@oai/artifact-tool`。
- 所有中间运行目录都放在当前任务输出目录；不要向 Skill 安装目录写依赖。
- 不安装网络依赖，不访问远程服务。

## 源包提取

```powershell
& <bundled-python> <skill-root>\scripts\build_source_packet.py `
  --output-dir <output-root>\audit `
  --pdftoppm <bundled-pdftoppm.exe> `
  --source <file-1> --source <file-2>
```

用户明确要求纳入隐藏或 CP/反馈 Sheet 时，分别增加 `--include-hidden-sheets` 或 `--include-review-sheets`。

## 规则与流水线校验

```powershell
& <bundled-python> <skill-root>\scripts\run_case_pipeline.py validate-rules
& <bundled-python> <skill-root>\scripts\run_case_pipeline.py validate-run --run-dir <output-root>\audit
& <bundled-python> <skill-root>\scripts\run_case_pipeline.py readiness-local --run-dir <output-root>\audit
```

## 工作簿构建

按 Spreadsheets Skill 的约束，在可写运行目录创建 junction，不修改 bundled 依赖目录：

```powershell
$runtime = Join-Path <output-root> '.workbook-runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
New-Item -ItemType Junction -Path (Join-Path $runtime 'node_modules') -Target <bundled-node-modules> | Out-Null
Copy-Item -LiteralPath <skill-root>\scripts\build_local_case_workbook.mjs -Destination (Join-Path $runtime 'build_local_case_workbook.mjs')
& <bundled-node> (Join-Path $runtime 'build_local_case_workbook.mjs') build `
  --template <skill-root>\assets\local-case-template.xlsx `
  --run-dir <output-root>\audit `
  --output-dir <output-root>

$readiness = Get-Content -Raw -LiteralPath '<output-root>\audit\delivery_readiness.json' | ConvertFrom-Json
$sidecar = Join-Path '<output-root>' ($readiness.output_filename + '.inspect.ndjson')
if (Test-Path -LiteralPath $sidecar) {
  Move-Item -LiteralPath $sidecar -Destination (Join-Path '<output-root>\audit' 'workbook-artifact-inspect.ndjson') -Force
}
```

构建器输出：根目录工作簿、`audit/workbook_readback.json`、`audit/workbook-preview-header.png` 和 `audit/workbook-preview.png`。

## 测试

```powershell
$env:QA_CASE_XLSX_PDFTOPPM = '<bundled-pdftoppm>'
$env:QA_CASE_XLSX_NODE = '<bundled-node>'
$env:QA_CASE_XLSX_NODE_MODULES = '<bundled-node-modules>'
& <bundled-python> -m unittest discover -s <skill-root>\tests -p 'test_*.py' -v
```

断网验收时把 `HTTP_PROXY`、`HTTPS_PROXY` 指向不可达本机端口再运行完整测试；测试必须不因网络失败而改变结果。
