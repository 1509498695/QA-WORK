[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$pluginRoot = (Split-Path -Parent $PSScriptRoot)
$pluginsRoot = (Split-Path -Parent $pluginRoot)
$repositoryRoot = (Split-Path -Parent $pluginsRoot)
$runtimeRoot = Join-Path $pluginRoot 'runtime'
$templatePath = Join-Path $pluginRoot 'runtime.template.toml'
$sourcePackages = @(
    @{
        Distribution = 'qa-skillhub-capability-contracts'
        Project = 'platform\capability-contracts\pyproject.toml'
        Package = 'platform\capability-contracts\src\capability_contracts'
    },
    @{
        Distribution = 'workspace-feishu-protocol'
        Project = 'providers\feishu\protocol\pyproject.toml'
        Package = 'providers\feishu\protocol\src\feishu_protocol'
    },
    @{
        Distribution = 'workspace-feishu-mcp-server'
        Project = 'providers\feishu\mcp-server\pyproject.toml'
        Package = 'providers\feishu\mcp-server\src\feishu_provider'
    }
)

function Resolve-ContainedPath {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Parent,
        [Parameter(Mandatory)] [string]$Label
    )
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $resolvedPath.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes its expected parent: $resolvedPath"
    }
    return $resolvedPath
}

function Get-ProjectVersion {
    param([Parameter(Mandatory)] [string]$Path)
    $match = Select-String -LiteralPath $Path -Pattern '^version\s*=\s*"([^"]+)"$' |
        Select-Object -First 1
    if ($null -eq $match -or $match.Matches.Count -ne 1) {
        throw "Project version is missing or ambiguous: $Path"
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-RelativeFileRecords {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [switch]$IgnoreRuntimeState
    )
    $records = @()
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File) {
        $relative = [System.IO.Path]::GetRelativePath($Root, $file.FullName).Replace('\', '/')
        if ($file.Extension -eq '.pyc' -or $relative -match '(^|/)__pycache__/') {
            continue
        }
        if (
            $IgnoreRuntimeState -and
            ($relative -eq 'uv.lock' -or $relative.StartsWith('.venv/'))
        ) {
            continue
        }
        $records += [pscustomobject]@{
            Path = $relative
            Hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return @($records | Sort-Object Path)
}

if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
    throw "Runtime project template is missing: $templatePath"
}

$temporaryBase = [System.IO.Path]::GetTempPath()
$temporaryRoot = Join-Path $temporaryBase ("qa-skillhub-runtime-" + [guid]::NewGuid().ToString('N'))
$temporaryRoot = Resolve-ContainedPath -Path $temporaryRoot -Parent $temporaryBase -Label 'Temporary runtime'
$stagedRuntime = Join-Path $temporaryRoot 'runtime'
[System.IO.Directory]::CreateDirectory((Join-Path $stagedRuntime 'src')) | Out-Null

try {
    Copy-Item -LiteralPath $templatePath -Destination (Join-Path $stagedRuntime 'pyproject.toml')
    $sourceManifest = @()
    foreach ($sourcePackage in $sourcePackages) {
        $projectPath = Join-Path $repositoryRoot $sourcePackage.Project
        $packagePath = Join-Path $repositoryRoot $sourcePackage.Package
        if (-not (Test-Path -LiteralPath $projectPath -PathType Leaf)) {
            throw "Runtime source project is missing: $projectPath"
        }
        if (-not (Test-Path -LiteralPath $packagePath -PathType Container)) {
            throw "Runtime source package is missing: $packagePath"
        }
        $targetPackage = Join-Path (Join-Path $stagedRuntime 'src') (Split-Path -Leaf $packagePath)
        [System.IO.Directory]::CreateDirectory($targetPackage) | Out-Null
        foreach ($sourceFile in Get-ChildItem -LiteralPath $packagePath -Recurse -File) {
            if (
                $sourceFile.Extension -eq '.pyc' -or
                $sourceFile.FullName -match '[\\/]__pycache__[\\/]'
            ) {
                continue
            }
            $relative = [System.IO.Path]::GetRelativePath($packagePath, $sourceFile.FullName)
            $targetFile = Join-Path $targetPackage $relative
            [System.IO.Directory]::CreateDirectory((Split-Path -Parent $targetFile)) | Out-Null
            Copy-Item -LiteralPath $sourceFile.FullName -Destination $targetFile
        }
        $sourceManifest += [ordered]@{
            distribution = $sourcePackage.Distribution
            version = Get-ProjectVersion -Path $projectPath
            project = [System.IO.Path]::GetRelativePath($repositoryRoot, $projectPath).Replace('\', '/')
            package = [System.IO.Path]::GetRelativePath($repositoryRoot, $packagePath).Replace('\', '/')
        }
    }

    $fileManifest = @(
        Get-RelativeFileRecords -Root $stagedRuntime |
            ForEach-Object {
                [ordered]@{
                    path = $_.Path
                    sha256 = $_.Hash
                }
            }
    )
    $manifest = [ordered]@{
        schema_version = 1
        plugin = 'workspace-feishu'
        runtime_version = Get-ProjectVersion -Path (Join-Path $stagedRuntime 'pyproject.toml')
        sources = $sourceManifest
        files = $fileManifest
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
        (Join-Path $stagedRuntime 'BUILD-MANIFEST.json'),
        $manifestJson + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    if ($Check) {
        if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
            throw "Plugin runtime is missing: $runtimeRoot"
        }
        $expected = Get-RelativeFileRecords -Root $stagedRuntime
        $actual = Get-RelativeFileRecords -Root $runtimeRoot -IgnoreRuntimeState
        $pathDifference = Compare-Object -ReferenceObject $expected.Path -DifferenceObject $actual.Path
        if ($pathDifference) {
            throw "Plugin runtime file set is stale:`n$($pathDifference | Out-String)"
        }
        $actualByPath = @{}
        foreach ($record in $actual) {
            $actualByPath[$record.Path] = $record.Hash
        }
        $changed = @(
            $expected |
                Where-Object { $actualByPath[$_.Path] -ne $_.Hash } |
                Select-Object -ExpandProperty Path
        )
        if ($changed.Count -gt 0) {
            throw "Plugin runtime content is stale: $($changed -join ', ')"
        }
        & uv lock --project $runtimeRoot --check
        if ($LASTEXITCODE -ne 0) {
            throw "Plugin runtime lock is stale."
        }
        Write-Output "Plugin runtime matches canonical sources and lock."
        exit 0
    }

    $validatedRuntime = Resolve-ContainedPath -Path $runtimeRoot -Parent $pluginRoot -Label 'Plugin runtime'
    $targetSource = Join-Path $validatedRuntime 'src'
    $expectedTargetSource = [System.IO.Path]::GetFullPath((Join-Path $pluginRoot 'runtime\src'))
    if ([System.IO.Path]::GetFullPath($targetSource) -ne $expectedTargetSource) {
        throw "Refusing to replace unexpected runtime source path: $targetSource"
    }
    [System.IO.Directory]::CreateDirectory($validatedRuntime) | Out-Null
    if (Test-Path -LiteralPath $targetSource -PathType Container) {
        Remove-Item -LiteralPath $targetSource -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $stagedRuntime 'src') -Destination $validatedRuntime -Recurse
    Copy-Item -LiteralPath (Join-Path $stagedRuntime 'pyproject.toml') -Destination $validatedRuntime -Force
    Copy-Item -LiteralPath (Join-Path $stagedRuntime 'BUILD-MANIFEST.json') -Destination $validatedRuntime -Force
    & uv lock --project $validatedRuntime
    if ($LASTEXITCODE -ne 0) {
        throw "Plugin runtime lock could not be generated."
    }
    Write-Output "Built self-contained plugin runtime at $validatedRuntime"
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot -PathType Container) {
        $validatedTemporary = Resolve-ContainedPath -Path $temporaryRoot -Parent $temporaryBase -Label 'Temporary cleanup'
        Remove-Item -LiteralPath $validatedTemporary -Recurse -Force
    }
}
