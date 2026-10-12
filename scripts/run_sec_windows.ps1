[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')]
    [string]$Contact,

    [Parameter()]
    [double]$MinimumInterval = 0.6
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Get-Command python -ErrorAction Stop

if ([string]::IsNullOrWhiteSpace($Contact)) {
    $Contact = $env:STOCKLENS_SEC_CONTACT
}
if ([string]::IsNullOrWhiteSpace($Contact) -or $Contact -match '(?i)noreply') {
    throw 'Set -Contact or STOCKLENS_SEC_CONTACT to a monitored email address.'
}

Push-Location $RepoRoot
try {
    & $Python.Source scripts/collect_sec_sample_windows.py `
        --contact $Contact `
        --minimum-interval $MinimumInterval
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

