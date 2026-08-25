[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $projectRoot
try {
    & uv run feishu-auth preflight
    if ($LASTEXITCODE -ne 0) {
        throw 'Feishu Provider is not configured. Run scripts\configure-local-auth.ps1 first.'
    }

    & uv run feishu-auth serve
    if ($LASTEXITCODE -ne 0) {
        throw "Feishu auth service exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
