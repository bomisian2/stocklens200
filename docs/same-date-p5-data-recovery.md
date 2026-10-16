# Same-Date P5.0 data recovery

This phase adds offline contracts only. It does not authorize or perform a
provider request, parse provider bytes into a candidate, or create a Same-Date
snapshot or investment decision.

## Supplemental Alpha Vantage plan

The previously captured Alpha Vantage terms PDF and AVGO
`TIME_SERIES_DAILY` response must not be requested again. A separately approved
supplemental run is limited to this order:

1. `https://www.alphavantage.co/query` with
   `function=TIME_SERIES_DAILY`, `symbol=NVDA`, `outputsize=compact`, and the API
   key supplied only as the in-memory `apikey` request parameter.
2. The same host/path with `function=EARNINGS_ESTIMATES` and `symbol=AVGO`.
3. The same host/path with `function=EARNINGS_ESTIMATES` and `symbol=NVDA`.

The API key and full query URL are never written. The hard request budget is
three including redirects, concurrency is one, automatic retries are zero, and
the project throttle is 60 seconds between requests. The official free-plan
review currently recorded by StockLens is 25 requests per day; the 60-second
interval is a conservative project throttle, not a claim of additional
provider capacity. A fresh execution approval must reconfirm the provider limit
review and that the user's remaining daily allowance is sufficient.

Any provider error/restriction envelope stops the run immediately. The original
terms bytes are linked by SHA-256; they are not fetched or copied into the new
run. A unique run ID, separate ledger, and separate terms-link manifest are
required. All outputs stay below ignored `private_data/`.

Zero-call plan inspection:

```powershell
.\.venv-py312\Scripts\python.exe scripts\collect_alpha_supplemental.py --show-plan
```

The `--execute` form remains prohibited until a new approval supplies a unique
run ID and approval reference.

## Provider-neutral local import contract

`config/provider-neutral-local-import-v1.schema.json` defines a local-only
sidecar manifest for a user-provided CSV or JSON file below
`private_data/imports/`. The read-only validator is
`stocklens.personal.local_import_contract.validate_provider_neutral_import`.

Every manifest records provider, ticker, immutable source SHA-256, `as_of`,
`available_at`, `captured_at`, accounting basis, FY1/FY2/NTM horizon, currency,
per-share unit, analyst count, revision/vintage, usage scope, price adjustment
policy, and explicit storage/derived-analysis/local-display rights.

Forward estimates fail closed when GAAP/non-GAAP or FY1/FY2/NTM is unknown.
Daily price files must explicitly state adjusted or unadjusted. Unknown rights,
future availability, hash mismatch, missing timestamps, path escape, and empty
source files are rejected. A validated synthetic fixture is labelled separately
from user-provided real evidence. No passing import is automatically promoted to
a parser candidate, snapshot, valuation, or decision.

## Existing AVGO compact daily data

The preserved 100-row, unadjusted AVGO compact series remains
`discovered_unverified`. It can support integrity diagnostics and recent price
flow research only. It cannot support long-horizon performance, dividend or
split-adjusted returns, a Forward PER ledger, valuation, buy zones, or an
operational signal.
