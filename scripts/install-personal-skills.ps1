[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Split-Path -Parent $PSScriptRoot)
$userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$skillsRoot = Join-Path $userProfile '.agents\skills'
$skillNames = @(
    'qa-case-xlsx-local',
    'qa-case-xlsx-unified'
)

foreach ($skillName in $skillNames) {
    $sourcePath = Join-Path $repositoryRoot (Join-Path 'skills' $skillName)
    $targetPath = Join-Path $skillsRoot $skillName

    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Canonical Skill source is missing: $sourcePath"
    }

    $resolvedSource = (Resolve-Path -LiteralPath $sourcePath).Path
    $existing = Get-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $targets = @($existing.Target | ForEach-Object { [string]$_ })
        $resolvedTargets = @(
            $targets |
                Where-Object { $_ } |
                ForEach-Object {
                    if (Test-Path -LiteralPath $_) {
                        (Resolve-Path -LiteralPath $_).Path
                    }
                }
        )
        if (
            $existing.LinkType -ne 'Junction' -or
            $resolvedTargets.Count -ne 1 -or
            $resolvedTargets[0] -ne $resolvedSource
        ) {
            throw "Personal Skill target already exists but is not the expected Junction: $targetPath"
        }
        Write-Output "Personal Skill Junction is ready: $targetPath -> $resolvedSource"
        continue
    }

    if ($Check) {
        throw "Personal Skill Junction is missing: $targetPath"
    }

    [System.IO.Directory]::CreateDirectory($skillsRoot) | Out-Null
    New-Item -ItemType Junction -Path $targetPath -Target $resolvedSource | Out-Null
    $created = Get-Item -LiteralPath $targetPath -Force
    if ($created.LinkType -ne 'Junction') {
        throw "Personal Skill target was created but is not a Junction: $targetPath"
    }
    Write-Output "Created personal Skill Junction: $targetPath -> $resolvedSource"
}
