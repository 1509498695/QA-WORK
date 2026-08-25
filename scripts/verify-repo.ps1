[CmdletBinding()]
param(
    [string]$CodexDependencyRoot = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Split-Path -Parent $PSScriptRoot)
$userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [string]$Command,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [string]$WorkingDirectory,
        [Parameter(Mandatory)] [string]$Label
    )
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

if (-not $CodexDependencyRoot) {
    $CodexDependencyRoot = Join-Path $userProfile '.cache\codex-runtimes\codex-primary-runtime\dependencies'
}
$CodexDependencyRoot = [System.IO.Path]::GetFullPath($CodexDependencyRoot)
if (-not (Test-Path -LiteralPath $CodexDependencyRoot -PathType Container)) {
    throw "Codex bundled dependency root is missing. Pass -CodexDependencyRoot with the path returned by the workspace dependency loader: $CodexDependencyRoot"
}

$uv = (Get-Command uv -ErrorAction Stop).Source
$rg = (Get-Command rg -ErrorAction Stop).Source
$python = Join-Path $CodexDependencyRoot 'python\python.exe'
$node = Join-Path $CodexDependencyRoot 'node\bin\node.exe'
$nodeModules = Join-Path $CodexDependencyRoot 'node\node_modules'
$pdftoppm = Join-Path $CodexDependencyRoot 'native\poppler\Library\bin\pdftoppm.exe'
foreach ($dependency in @($python, $node, $nodeModules, $pdftoppm)) {
    if (-not (Test-Path -LiteralPath $dependency)) {
        throw "Required bundled dependency is missing: $dependency"
    }
}

Invoke-Checked -Command $uv -Arguments @('lock', '--check') -WorkingDirectory $repositoryRoot -Label 'Workspace lock check'
Invoke-Checked -Command $uv -Arguments @('run', '--locked', 'pytest', '-q') -WorkingDirectory $repositoryRoot -Label 'Workspace tests'
Invoke-Checked -Command $uv -Arguments @(
    'run', '--locked', 'python', '-m', 'compileall', '-q',
    'platform/capability-contracts/src',
    'platform/capability-contracts/tests',
    'providers/feishu/protocol/src',
    'providers/feishu/protocol/tests',
    'providers/feishu/auth-service/src',
    'providers/feishu/auth-service/tests',
    'providers/feishu/mcp-server/src',
    'providers/feishu/mcp-server/tests',
    'providers/feishu/tests/integration'
) -WorkingDirectory $repositoryRoot -Label 'Python compile check'

$mcpLeak = & $rg -n 'feishu_auth_service' 'providers/feishu/mcp-server/src'
if ($LASTEXITCODE -eq 0) {
    throw "MCP Server imports Auth Service implementation:`n$($mcpLeak -join [Environment]::NewLine)"
}
if ($LASTEXITCODE -gt 1) {
    throw "MCP dependency boundary scan failed with exit code $LASTEXITCODE."
}
$contractLeak = & $rg -n 'FEISHU|feishu|larksuite|resolve_feishu|TargetKind|ResourceType|ResourceLocator' 'platform/capability-contracts/src'
if ($LASTEXITCODE -eq 0) {
    throw "Provider-specific semantics leaked into capability contracts:`n$($contractLeak -join [Environment]::NewLine)"
}
if ($LASTEXITCODE -gt 1) {
    throw "Capability contract boundary scan failed with exit code $LASTEXITCODE."
}

& (Join-Path $repositoryRoot 'plugins\workspace-feishu\scripts\build_runtime.ps1') -Check
if ($LASTEXITCODE -ne 0) {
    throw "Plugin runtime source check failed with exit code $LASTEXITCODE."
}

$pluginValidator = Join-Path $userProfile '.codex\skills\.system\plugin-creator\scripts\validate_plugin.py'
if (-not (Test-Path -LiteralPath $pluginValidator -PathType Leaf)) {
    throw "Codex plugin validator is missing: $pluginValidator"
}
Invoke-Checked -Command $uv -Arguments @(
    'run', '--with', 'pyyaml', $pluginValidator,
    (Join-Path $repositoryRoot 'plugins\workspace-feishu')
) -WorkingDirectory $repositoryRoot -Label 'Plugin manifest validation'
Invoke-Checked -Command $uv -Arguments @(
    'run', '--project', 'plugins/workspace-feishu/runtime', '--locked',
    'python', '-c',
    'import asyncio; from feishu_provider.mcp_server import build_server; assert len(asyncio.run(build_server().list_tools())) == 4'
) -WorkingDirectory $repositoryRoot -Label 'Plugin runtime smoke test'

& (Join-Path $repositoryRoot 'scripts\install-personal-skills.ps1') -Check
if ($LASTEXITCODE -ne 0) {
    throw "Personal Skill discovery check failed with exit code $LASTEXITCODE."
}

$skillRoot = Join-Path $repositoryRoot 'skills\qa-case-xlsx-local'
$previousNode = $env:QA_CASE_XLSX_NODE
$previousNodeModules = $env:QA_CASE_XLSX_NODE_MODULES
$previousPdftoppm = $env:QA_CASE_XLSX_PDFTOPPM
$previousNodePath = $env:NODE_PATH
try {
    $env:QA_CASE_XLSX_NODE = $node
    $env:QA_CASE_XLSX_NODE_MODULES = $nodeModules
    $env:QA_CASE_XLSX_PDFTOPPM = $pdftoppm
    $env:NODE_PATH = $nodeModules
    Invoke-Checked -Command $python -Arguments @(
        '-m', 'unittest', 'discover', '-s', 'tests', '-v'
    ) -WorkingDirectory $skillRoot -Label 'Business Skill tests'
}
finally {
    $env:QA_CASE_XLSX_NODE = $previousNode
    $env:QA_CASE_XLSX_NODE_MODULES = $previousNodeModules
    $env:QA_CASE_XLSX_PDFTOPPM = $previousPdftoppm
    $env:NODE_PATH = $previousNodePath
}

Invoke-Checked -Command 'git' -Arguments @('diff', '--check') -WorkingDirectory $repositoryRoot -Label 'Git whitespace check'
Write-Output 'QA Skill Hub verification passed: workspace=86 tests, business-skill=18 tests, plugin-runtime=verified.'
