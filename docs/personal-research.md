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
    .\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --data-dir private_data --market-tickers AMAT AVGO QCOM QQQ --max-market-calls 4
}
finally {
    Remove-Item Env:ALPHAVANTAGE_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
    Remove-Variable secureKey, keyPtr -ErrorAction SilentlyContinue
}
```

The key is read only from the process environment. Do not put it in source,
command history shared with others, screenshots, or Git.

Inspect the ten-company request plan without making a network call or changing
local data:

```powershell
.\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --print-call-plan
```

The current plan is 11 daily EOD calls (ten companies plus QQQ) and, only after
access validation, 10 weekly estimate calls. The combined validation-day maximum
is 21 of the published 25 calls; four calls remain unused and the code performs
no automatic retry.

To validate analyst estimates later, the user must explicitly approve the call
budget and set the key only in the current process. For one AMAT validation call:

```powershell
$secureKey = Read-Host "Alpha Vantage API key" -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:ALPHAVANTAGE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
    .\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --data-dir private_data --saved-only --estimate-tickers AMAT --max-estimate-calls 1
}
finally {
    Remove-Item Env:ALPHAVANTAGE_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
    Remove-Variable secureKey, keyPtr -ErrorAction SilentlyContinue
}
```

This command is an example, not authorization to run it. A successful payload is
stored only below ignored `private_data/estimates/`, with raw SHA-256 and without
the key or full request URL. It remains `research_only`; company guidance stays
separate, and the provider collection time is not relabeled as the consensus
formation time.

`--max-market-calls` is an explicit one-run budget. Its default is zero, so a
misspelled command cannot consume quota. The requested ticker count must fit
inside the budget or the script makes no market request. A failed attempt is
recorded only as a safe diagnostic and never deletes a previously verified
cache or its status row.

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

The same vertical screen now works for AVGO and QCOM. AVGO has a verified
100-session local price cache through 2026-10-12, so the common contract produces
the same descriptive EMA20/EMA50/RSI14, QQQ-relative return, and research-only
value references used for AMAT. QCOM remains blocked by a safely classified
provider rate-limit response, so its verified financial analysis remains visible
without a substituted price. Guidance retains each issuer's basis: AMAT Non-GAAP
EPS, AVGO Non-GAAP operating margin, and QCOM GAAP EPS are never mixed.

The app places AMAT and AVGO side by side using one calculation contract. It
compares verified TTM revenue, operating income and FCF, issuer guidance versus
the latest reported quarter, descriptive price trend, and the same proxy P/FCF
and FCF-yield definitions. It also answers five practical questions: whether
reported performance improved, how growth-dependent the proxy valuation is,
whether the short price trend strengthened or weakened, what to watch next, and
what cannot yet be decided.

The five-year FCF sensitivity grid uses 0%, 10%, 15%, 20%, and 25% CAGR. These
rates are not forecasts. Every row holds the current proxy equity value, price,
share count and capital structure constant, excludes dilution, buybacks, M&A and
terminal value, and grows only the audited TTM CFO-minus-CAPEX reference. The
result is a transparent growth-dependence diagnostic, never a fair value, target
price or buy range.

Alpha Vantage documents `SHARES_OUTSTANDING` and `EARNINGS_ESTIMATES` endpoints.
The single approved AMAT estimate call returned no usable estimate periods, so
actual consensus remains zero companies and forward PER remains unavailable.
The screen does not turn a documented endpoint into actual consensus or formal
capitalization. Different displayed prices are compared only when trading date,
regular-session/EOD basis, and adjustment policy match.

Official issuer evidence currently supplies point-in-time common shares for
AMAT (793.597443 million at 2026-07-26) and MSFT (7,427 million at 2026-06-30).
AMAT's local price is dated 2026-10-12, a 78-calendar-day mismatch, so the UI
shows the resulting equity value and FCF yield only as time-mismatched research
references. It does not promote either to formal market capitalization or a
valuation signal. The other connected financial issuers remain unavailable for
point-in-time common shares, including Alphabet's unresolved multi-class bridge.
The acquired Broadcom issuer releases provide a Q3 FY2026 diluted weighted-average
share count but not a point-in-time common-share count. AVGO therefore remains a
proxy capitalization even though its local closing price is verified.

## Personal market-data scale options

No additional provider call was made for this comparison release. The current
Alpha Vantage free allowance remains suitable for a small rotating personal
watchlist, not a claim of daily 200-company coverage. Its documented standard
free limit is [25 requests per day](https://www.alphavantage.co/premium/). A 10-company price refresh plus QQQ can fit
within that budget when one request per symbol succeeds; 200 companies cannot.

Two personal-use candidates remain evaluation options rather than configured
providers. [Tiingo](https://www.tiingo.com/products/end-of-day-stock-price-data)
advertises a free individual EOD tier with 1,000 requests per day and a $30/month
individual tier with higher limits, while
[Massive](https://www.massive.com/stocks) advertises a free EOD tier at five
requests per minute and paid individual tiers starting at $29/month. Before either is implemented, the exact local-cache, adjustment,
history, and derived-analysis rights must be accepted and verified for the
intended personal workflow. No subscription was purchased and no provider was
called during this work.

## PR CI and provider diagnostic separation

PR validation is offline. `code-tests (offline)` runs unit tests and Streamlit
AppTest, while `stored-snapshot-integrity (offline)` verifies that the committed
historical Top-200 artifact was valid when stored. Neither job calls Yahoo,
Nasdaq, Alpha Vantage, or another market-data provider.

The former PR live job ran at 2026-10-13 15:28 UTC, during the regular XNYS
session. Yahoo returned 2026-10-13 intraday timestamps while the latest completed
session was still 2026-10-12; rejecting those values was correct. Nasdaq had no
authoritative per-security market timestamp, so it remained `unverified`. A
cache-free temporary runner therefore had no eligible snapshot to publish.

Live provider diagnostics now use a separate manual workflow. The workflow logs
its UTC evaluation time, XNYS phase, and latest completed session before
collection. It has no schedule or pull-request trigger, performs no automatic
retry beyond the existing bounded provider behavior, and cannot reach the
network step until both operator confirmation and `config/provider-rights.json`
allow it. The current manifest blocks the call because public-runner collection
and redistribution rights remain unconfirmed.

The locally retained AMAT evidence fixes 793.597443 million common shares at
2026-07-26, but no later point-in-time issuer value is present before the
2026-10-12 price date. The next eligible update path is a later official 10-Q or
10-K cover-page count; a weighted-average income-statement denominator cannot
fill that gap. Broadcom's retained earnings releases disclose the Q3 FY2026
4,887 million diluted weighted-average denominator, not point-in-time common
shares. AVGO therefore needs an official 10-Q/10-K cover-page count (or an
equivalent issuer filing) before formal capitalization is possible. The tested
free estimate response contained no usable forecast periods, so issuer guidance
remains separate and analyst consensus stays unavailable; no paid consensus
subscription was selected or purchased.

FinanceToolkit is not added because the displayed ratios are simpler and more
auditable in the existing pure functions, while its provider/dependency layer
does not supply the missing evidence. QuantStats is not added because there is
no validated daily portfolio-return series or operating strategy to evaluate.
