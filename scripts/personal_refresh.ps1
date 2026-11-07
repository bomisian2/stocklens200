param(
    [string]$DataDir = "private_data",
    [string[]]$MarketTickers = @()
)

$ErrorActionPreference = "Stop"
$arguments = @("scripts/personal_refresh.py", "--data-dir", $DataDir)
if ($MarketTickers.Count -gt 0) {
    $arguments += "--market-tickers"
    $arguments += $MarketTickers
}

python @arguments
exit $LASTEXITCODE
