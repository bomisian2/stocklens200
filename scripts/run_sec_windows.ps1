[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')]
    [string]$Contact,

    [Parameter()]
    [double]$MinimumInterval = 0.6,

    [Parameter()]
    [string]$ValuationEvidence
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
    $Arguments = @(
        'scripts/collect_sec_sample_windows.py',
        '--contact', $Contact,
        '--minimum-interval', $MinimumInterval
    )
    if (-not [string]::IsNullOrWhiteSpace($ValuationEvidence)) {
        $Arguments += @('--valuation-evidence', $ValuationEvidence)
    }
    & $Python.Source @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

