# Same-Date P5.0.1 data recovery

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

Every unsuccessful capture stops the run before another sleep or request. This
includes non-200 HTTP status, network timeout or connection failure, incomplete
or partial capture, invalid content type or JSON structure, ticker mismatch,
provider error/restriction envelope, redirect-budget exhaustion, or any result
whose `valid_provider_dataset` is not true. The original terms bytes are linked
by SHA-256; they are not fetched or copied into the new run. A unique run ID,
separate ledger, and separate terms-link manifest are required. All outputs
stay below ignored `private_data/`.

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
source files are rejected. This stage validates only the evidence container,
manifest, timestamps, hash, and rights contract. It does not inspect source-row
ticker, dates, values, currency, horizon, accounting basis, or analyst count,
so `row_semantics_validated=false` and `real_data_eligible=false` remain
mandatory. A validated synthetic fixture is labelled separately from
user-provided real evidence. No passing import is automatically promoted to a
parser candidate, snapshot, valuation, or decision.

## Existing AVGO compact daily data

The preserved 100-row, unadjusted AVGO compact series remains
`discovered_unverified`. It can support integrity diagnostics and recent price
flow research only. It cannot support long-horizon performance, dividend or
split-adjusted returns, a Forward PER ledger, valuation, buy zones, or an
operational signal.

## P5.0.2 offline response-shape adjudication

The approved supplemental run
`p501-e0f7040-supplemental-approved-01` stopped after its second request. The
preserved AVGO response uses the provider's `estimates[]` shape rather than the
legacy annual/quarterly top-level arrays. P5.0.2 validates those shapes through
separate, unambiguous paths. The array shape requires an exact ticker, a
non-empty list, ISO dates, `fiscal quarter` or `fiscal year` horizons, finite
EPS/revenue estimates, and non-negative integral analyst counts. Optional
revision and prior-estimate fields may be null, but unknown fields and mixed
legacy/array payloads are rejected.

Structural acceptance does not establish whether estimates are GAAP or
non-GAAP. It also does not prove when a historical-period estimate first became
available. Therefore the preserved AVGO response remains
`accounting_basis=unknown`, `historical_availability_verified=false`,
`parser_candidate=false`, `decision_created=false`, and
`operational_eligible=false`. Dates before capture are classified only as past
periods visible in the current snapshot. FY1/NTM mapping and historical
backtesting remain prohibited.

The offline command below writes one immutable adjudication under ignored
`private_data/decision/`. It hash-links the raw response, capture manifest, and
supplemental ledger without changing any of them:

```powershell
.\.venv-py312\Scripts\python.exe scripts\adjudicate_alpha_supplemental.py `
  --run-id "p501-e0f7040-supplemental-approved-01" `
  --generated-at "<timezone-aware UTC timestamp>"
```

Synthetic fixtures use `evidence_origin=synthetic_fixture` and can never prove
that actual provider evidence passed. The local preserved run uses
`evidence_origin=local_provider_evidence` but remains non-promoting until a
separate accounting-basis and rights review succeeds.

## P5.0.3 one-request continuation (offline implementation only)

P5.0.3 does not resume or modify the failed supplemental run. Its separate
collector permits exactly one logical operation and one actual HTTP request:

1. `request_id=nvda-estimates`, `function=EARNINGS_ESTIMATES`, `symbol=NVDA`.

Before any request, the collector re-hashes and validates the immutable prior
supplemental ledger, AVGO estimates raw bytes and capture manifest, and the
P5.0.2 adjudication. It also proves that the prior ledger contains only
`nvda-daily` and `avgo-estimates`, so no prior `nvda-estimates` result exists.
Hash mismatch, output collision, run ID reuse, a missing API key, or any budget
other than exactly one blocks before network access. Redirects are disabled;
the sole redirect response consumes the only budget and fails closed without a
second HTTP request.

The zero-call plan is available with:

```powershell
.\.venv-py312\Scripts\python.exe scripts\collect_alpha_continuation.py --show-plan
```

The execution path requires a future, independent approval with a new run ID
and approval reference. Even a structurally valid response remains
`accounting_basis=unknown`, `historical_availability_verified=false`,
`fy1_ntm_mapping_eligible=false`, `parser_candidate=false`,
`snapshot_created=false`, `decision_created=false`, and
`operational_eligible=false`. Records dated before collection remain current
snapshot records for past periods and are not historical PIT evidence.
