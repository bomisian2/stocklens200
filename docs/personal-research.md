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
.\.venv-personal\Scripts\streamlit.exe run personal_app.py
```

The first refresh collects official issuer financial releases for AMAT, AVGO,
and QCOM. Price collection remains disabled unless the user supplies their own
Alpha Vantage key:

```powershell
$env:ALPHAVANTAGE_API_KEY = "your-own-key"
.\.venv-personal\Scripts\python.exe scripts\personal_refresh.py --data-dir private_data --market-tickers AMAT AVGO QCOM
```

The key is read only from the process environment. Do not put it in source,
command history shared with others, screenshots, or Git.

## Data contract

Each `private_data/normalized/financials/{TICKER}.json` contains:

- FY2025, Q3 FY2026, nine-month FY2026, and a derived TTM period;
- reported GAAP revenue, operating income, net income, diluted EPS/shares where
  the same period denominator is available, CFO, CAPEX, and FCF;
- current balance-sheet cash/investments/debt;
- issuer guidance kept separately from reported results;
- source URL, publication time, period end, byte length, and SHA-256;
- explicit local-only and redistribution-not-authorized flags.

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
responses, and missing table anchors produce `blocked`; prior good files remain.
The UI never labels collection time as market time or publication time.
