[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$pluginRoot = (Split-Path -Parent $PSScriptRoot)
$pluginsRoot = (Split-Path -Parent $pluginRoot)
$repositoryRoot = (Split-Path -Parent $pluginsRoot)
$sourceRoot = Join-Path $repositoryRoot "src"
$targetRoot = Join-Path $pluginRoot "runtime\src"
$packages = @(
    "capability_contracts",
    "feishu_auth_service",
    "feishu_provider"
)

foreach ($package in $packages) {
    $sourcePackage = Join-Path $sourceRoot $package
    $targetPackage = Join-Path $targetRoot $package
    if (-not (Test-Path -LiteralPath $sourcePackage -PathType Container)) {
        throw "Runtime source package is missing: $sourcePackage"
    }
    New-Item -ItemType Directory -Path $targetPackage -Force | Out-Null

    $sourceFiles = Get-ChildItem -LiteralPath $sourcePackage -Filter "*.py" -File
    $sourceNames = @($sourceFiles.Name | Sort-Object)
    $staleNames = @(
        Get-ChildItem -LiteralPath $targetPackage -Filter "*.py" -File |
            Where-Object { $_.Name -notin $sourceNames } |
            Select-Object -ExpandProperty Name
    )
    if ($staleNames.Count -gt 0) {
        throw "Refusing to delete stale packaged files in ${targetPackage}: $($staleNames -join ', ')"
    }
    foreach ($sourceFile in $sourceFiles) {
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $targetPackage -Force
    }
}

Write-Output "Synchronized plugin runtime from $sourceRoot to $targetRoot"
