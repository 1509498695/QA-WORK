[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $projectRoot
try {
    $preflightLines = & uv run feishu-auth preflight
    $preflightExitCode = $LASTEXITCODE
    if ($preflightExitCode -ne 0) {
        throw 'Feishu Provider deployment binding or Profile store is not ready.'
    }
    try {
        $preflight = ($preflightLines -join [Environment]::NewLine) |
            ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw 'Feishu Provider preflight did not return its expected JSON contract.'
    }
    if ([int]$preflight.authorized_profiles -lt 1) {
        throw 'No authorized Feishu Profile is available. Complete OAuth in the local auth service first.'
    }

    $listener = Get-NetTCPConnection `
        -LocalAddress '127.0.0.1' `
        -LocalPort 3000 `
        -State Listen `
        -ErrorAction SilentlyContinue
    if (-not $listener) {
        throw 'Start scripts\run-local-auth.ps1 before the Feishu Provider MCP.'
    }

    & uv run feishu-provider
    if ($LASTEXITCODE -ne 0) {
        throw "Feishu Provider MCP exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
