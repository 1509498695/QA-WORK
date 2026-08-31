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
$compileCacheBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$compileCache = [System.IO.Path]::GetFullPath(
    (Join-Path $compileCacheBase ('qa-skill-hub-compile-' + [guid]::NewGuid().ToString('N')))
)
if (-not $compileCache.StartsWith($compileCacheBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Compile cache escaped the system temporary directory: $compileCache"
}
$previousCompileCache = $env:PYTHONPYCACHEPREFIX
try {
    $env:PYTHONPYCACHEPREFIX = $compileCache
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
}
finally {
    $env:PYTHONPYCACHEPREFIX = $previousCompileCache
    if (Test-Path -LiteralPath $compileCache -PathType Container) {
        Remove-Item -LiteralPath $compileCache -Recurse -Force
    }
}

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
$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Invoke-Checked -Command $uv -Arguments @(
        'run', '--isolated', '--project', 'plugins/workspace-feishu/runtime', '--locked',
        'python', '-c',
        'import asyncio; from feishu_provider.mcp_server import build_server; assert len(asyncio.run(build_server().list_tools())) == 8'
    ) -WorkingDirectory $repositoryRoot -Label 'Plugin runtime smoke test'
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodeSetting
}

& (Join-Path $repositoryRoot 'scripts\install-personal-skills.ps1') -Check
if ($LASTEXITCODE -ne 0) {
    throw "Personal Skill discovery check failed with exit code $LASTEXITCODE."
}

$skillValidator = Join-Path $userProfile '.codex\skills\.system\skill-creator\scripts\quick_validate.py'
if (-not (Test-Path -LiteralPath $skillValidator -PathType Leaf)) {
    throw "Codex Skill validator is missing: $skillValidator"
}
$previousPythonUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    foreach ($candidateSkillRoot in @(
        (Join-Path $repositoryRoot 'skills\qa-case-xlsx-local'),
        (Join-Path $repositoryRoot 'skills\qa-case-xlsx-unified')
    )) {
        Invoke-Checked -Command $uv -Arguments @(
            'run', '--with', 'pyyaml', $skillValidator, $candidateSkillRoot
        ) -WorkingDirectory $repositoryRoot -Label "Skill validation: $candidateSkillRoot"
    }
}
finally {
    $env:PYTHONUTF8 = $previousPythonUtf8
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

$unifiedSkillRoot = Join-Path $repositoryRoot 'skills\qa-case-xlsx-unified'
Invoke-Checked -Command $python -Arguments @(
    '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'
) -WorkingDirectory $unifiedSkillRoot -Label 'Unified business Skill tests'

Invoke-Checked -Command 'git' -Arguments @('diff', '--check') -WorkingDirectory $repositoryRoot -Label 'Git whitespace check'
Write-Output 'QA Skill Hub verification passed: workspace, business Skill, and plugin runtime verified.'
