# StockLens 200 local personal research mode

This branch adds a local-only data path. It does not change `app.py`, the public
snapshot under `data/`, the public refresh workflow, or the production site.

## Boundary

- Raw and normalized personal data are written below `private_data/`.
- `private_data/`, `.streamlit/secrets.toml`, and `personal_logs/` are ignored by Git.
- `personal_app.py` reads local files only. It performs no network request.
- Research outputs are not written to `data/universe.csv`, valuation CSV files,
  public signals, alerts, or the public Streamlit application.
- No stored manifest contains an API key. Provider response bodies are not put in
  errors or logs.

## One-time Windows setup and run

Copy and run this block from the repository root:

```powershell
py -3.12 -m venv .venv-personal
.\.venv-personal\Scripts\python.exe -m pip install -r requirements.txt -r requirements-personal.txt
.\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --data-dir private_data
.\.venv-personal\Scripts\python.exe scripts\audit_personal_data.py --data-dir private_data
.\.venv-personal\Scripts\python.exe scripts\audit_personal_market.py --data-dir private_data
.\.venv-personal\Scripts\streamlit.exe run personal_app.py
```

The first refresh collects official issuer financial releases for AMAT, AVGO,
and QCOM. Price collection remains disabled unless the user supplies their own
Alpha Vantage key:

```powershell
$secureKey = Read-Host "Alpha Vantage API key" -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:ALPHAVANTAGE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
    .\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --data-dir private_data --market-tickers AMAT AVGO QCOM QQQ
}
finally {
    Remove-Item Env:ALPHAVANTAGE_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
    Remove-Variable secureKey, keyPtr -ErrorAction SilentlyContinue
}
```

The key is read only from the process environment. Do not put it in source,
command history shared with others, screenshots, or Git.

The free price call returns at most the latest 100 raw, unadjusted sessions and
is merged into the ignored local cache. Consequently EMA20/EMA50/RSI14 may be
available after the first successful call, but EMA200 and the 252-session
drawdown remain null until enough locally accumulated sessions exist. QQQ
relative strength is calculated only from dates present in both caches.

The market audit is offline and makes zero provider requests. It checks OHLCV
ranges, duplicate dates, XNYS-session gaps, manifest date and SHA-256, indicator
eligibility, and aligned QQQ relative strength. Its result is stored only in
`private_data/audit/market/summary.json`.

Future blocked responses are reduced to safe diagnostic categories such as
`rate_limit`, `entitlement_required`, `authentication_failed`, and
`invalid_request_or_symbol`. The API key, full request URL, and provider response
body are never stored. Runs made by the older adapter remain
`legacy_unclassified_response`; their exact cause must not be guessed.

## Data contract

Each `private_data/normalized/financials/{TICKER}.json` contains:

- FY2025, Q3 FY2026, nine-month FY2026, and a derived TTM period;
- reported GAAP revenue, operating income, net income, diluted EPS/shares where
  the same period denominator is available, CFO, CAPEX, and FCF;
- current balance-sheet cash/investments/debt;
- issuer guidance kept separately from reported results;
- source URL, publication time, period end, byte length, and SHA-256;
- explicit local-only and redistribution-not-authorized flags.

Before replacing a prior normalized snapshot, the collector now compares every
reported figure, TTM formula input, source URL, publication timestamp, byte
length, and SHA-256 with an independent issuer-fact contract. A mismatch blocks
replacement. The standalone audit writes only beneath ignored `private_data/`.

TTM is `FY2025 + current 9M - prior 9M`. TTM diluted EPS is intentionally null:
the two documents do not provide one consistent TTM weighted-average diluted
share denominator. A plausible-looking EPS is not fabricated.

## Windows Task Scheduler design (not yet proven)

After manual validation, a user can create a task that runs the wrapper from the
repository directory. The example below is a design, not proof of a scheduled run:

```powershell
schtasks /Create /TN "StockLens Personal Refresh" /SC DAILY /ST 08:30 /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\GPT\stocklens200\scripts\personal_refresh.ps1 -DataDir D:\GPT\stocklens200\private_data" /F
```

Use an account that can read the repository and write `private_data`. Verify the
first real `Task Scheduler` event and the resulting `status.json` timestamp before
calling scheduling successful. A manual run is not scheduler evidence.

## Failure behavior

Both source documents for a ticker must download and parse before its previous
raw and normalized snapshot is replaced. HTTP errors, unexpected content, small
responses, and missing table anchors produce `blocked`; prior good files remain
and are counted separately as `financial_tickers_available`, never as a newly
verified refresh.
The UI never labels collection time as market time or publication time.

## AMAT vertical analysis MVP

`personal_app.py` now connects the already-audited AMAT financial snapshot to
the local AMAT and QQQ caches in one read-only screen. It adds no network call
and writes no derived value to the public app or operational signal files.

The screen distinguishes three layers:

- verified facts: issuer financial periods, guidance, local EOD date, and
  descriptive EMA/RSI calculations;
- research-only references: a capitalization proxy using the latest-quarter
  diluted weighted-average shares, its proxy FCF yield, and an FY GAAP EPS
  reference multiple;
- unavailable operational outputs: formal market capitalization, TTM PER,
  fair value, buy zone, and independently validated trading signals.

The weighted-average share count is not relabeled as point-in-time common
shares outstanding. Reverse DCF remains `underdetermined` until a verified
forecast, WACC, terminal assumptions, and a consistent share denominator are
available. With only 100 raw, unadjusted sessions, EMA200, the 52-week range,
and PR #3's independent-validation gate remain unavailable.

The same vertical screen now works for AVGO and QCOM even when price collection
is blocked. Their verified FY/quarter/YTD/TTM financials, issuer guidance,
accounting-quality warnings, and follow-up conditions remain visible; price,
technical, market-cap, yield, and multiple fields stay unavailable. Guidance is
rendered from a common contract while retaining each issuer's basis: AMAT
Non-GAAP EPS, AVGO Non-GAAP operating margin, and QCOM GAAP EPS are never mixed.

Alpha Vantage documents `SHARES_OUTSTANDING` and `EARNINGS_ESTIMATES` endpoints,
but no ticker-level response or reuse eligibility has been validated in this
branch. The screen therefore does not turn those documented capabilities into
actual consensus, forward PER, or formal capitalization. Different displayed
prices are compared only when trading date, regular-session/EOD basis, and
adjustment policy match.

FinanceToolkit is not added because the displayed ratios are simpler and more
auditable in the existing pure functions, while its provider/dependency layer
does not supply the missing evidence. QuantStats is not added because there is
no validated daily portfolio-return series or operating strategy to evaluate.
