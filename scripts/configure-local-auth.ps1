[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $projectRoot
try {
    & uv run --package workspace-feishu-auth-service --locked feishu-auth configure
    $configurationExitCode = $LASTEXITCODE

    switch ($configurationExitCode) {
        0 {
            & uv run --package workspace-feishu-auth-service --locked feishu-auth preflight
            if ($LASTEXITCODE -ne 0) {
                throw 'Saved configuration did not pass preflight.'
            }
            & uv run --package workspace-feishu-auth-service --locked feishu-auth serve
            if ($LASTEXITCODE -ne 0) {
                throw "Feishu auth service exited with code $LASTEXITCODE."
            }
        }
        4 {
            Write-Host 'Local Feishu deployment binding was deleted. OAuth service remains stopped.'
        }
        5 {
            Write-Host 'Configuration session was cancelled. No service was started.'
        }
        6 {
            Write-Host 'Configuration session expired. No service was started.'
        }
        3 {
            throw 'Stop the OAuth service before opening the configuration page.'
        }
        default {
            throw "Configuration session exited with code $configurationExitCode."
        }
    }
}
finally {
    Pop-Location
}
