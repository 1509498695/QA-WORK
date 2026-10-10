[CmdletBinding()]
param(
    [int]$Port = 3100,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Split-Path -Parent $PSScriptRoot)
$uv = (Get-Command uv -ErrorAction Stop).Source
$arguments = @(
    'run', '--project', (Join-Path $repositoryRoot 'platform\control-plane'),
    'qa-skill-hub-control-plane', '--host', '127.0.0.1', '--port', [string]$Port
)
if ($OpenBrowser) {
    $arguments += '--open-browser'
}

& $uv @arguments
exit $LASTEXITCODE
