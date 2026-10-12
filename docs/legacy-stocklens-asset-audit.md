# Legacy StockLens asset audit

Audit date: 2026-10-12 (Asia/Seoul)  
Branch: `feat/v2-valuation-engine`

## Corrected conclusion

The original dashboard source was found at the local read-only path
`C:\Users\<local-user>\Desktop\클로드\files`. The earlier audit of
`D:\GPT\StockLens200_prototype` examined a different prototype and must not be used to conclude that
the legacy features were absent.

The current legacy cache contains 41 tickers, while the source still contains 39 `analystLog` blocks and
comments that describe the earlier 39-stock stage. The audit did not alter any legacy file.

## Reproducible inventory

| Asset | Observed structure | Count / state | SHA-256 |
|---|---|---:|---|
| `investment_compass_dashboard.html` | current single-file dashboard and price-validity logic | 1,337,148 bytes | `874436d76a93fcfd1b1768d336cfec12f51cc0ed451e1e71c55a9ac0032e195c` |
| `business_notes_data.js` | qualitative company notes and `analystLog` | 39 logs, 39 `oneTime` narratives, 10 boolean distortion flags | `dc667e57e210369c798720aefc37a5507fa488fb3f486749a1a85b77e3b9380a` |
| `financials_cache.json` | annual and quarterly financial cache | 41 tickers | `4eed1c459c1c44e13e2228219a175db5adf4b02467ce5d97da1a96b461cb4d8e` |
| `fundamentals_cache.json` | current/aggregate multiples and bands | 41 tickers | `147be108b5a8de48a58ac1ef136a3a053d696dacc8b71361cb49e87be163e0ca` |
| `review_baseline_data.js` | signature baseline | empty object | `83dcca9d06f3f0eaa7c1fa745cddd61015caa5213032d91bbb3927464b64b640` |

Historical source snapshots also exist as `.bak_pre_51748...` through `.bak_pre_51752...`, dated
2026-10-11 local time. They preserve the price-validity redesign sequence but do not add raw
point-in-time observations.

## Requested feature findings

| Feature | What actually exists | v2.1 reuse decision |
|---|---|---|
| `bandTable` | Runtime output created inside `computePriceValidity`: interpolated NTM EPS multiplied by aggregate `avgPe3Y` and low/high factors | Not a historical P/E observation table; cannot populate v2.1 P/E evidence |
| `analystLog.oneTime` | 39 narrative fields; 10 newest records carry `epsOneTimeDistortion` boolean | Reference-only. No uniform after-tax per-share adjustment, publication timestamp, or filing identity |
| `computePriceValidity` | Present in the current dashboard | Do not port wholesale: it deliberately removed price-hold behavior and can force a PEG/fallback label despite missing evidence |
| distorted / PEG logic | `avgPe3Y / NTM P/E > 3` flag plus PEG fallback and vetoes | Useful as design history, not validated evidence or a calibrated model |
| confidence | Three heuristic penalty buckets | Display concept is reusable; scores are not calibrated and must not be migrated as v2.1 confidence |
| signature review queue | `pvReviewSignature(ticker, code, value, sourceDate, basis)` and delta/open-constraint queue | Signature concept is reusable after schema mapping; deployed baseline is currently empty |

`fundamentals_cache.json` has 37 `avgPe3Y` values, 33 ticker low/high bands, and 41
`perBandYears` fields. It does **not** contain the dated prices, then-public EPS vintages, GAAP basis,
split state, source URLs, or observation rows needed to recreate those aggregates. GOOGL reports
`perBandYears=22`, but this is a scalar label, not 22 auditable observations.

## GOOGL legacy data check

The legacy financial cache has eight annual and twelve quarterly GOOGL rows. Its 2022-2025 annual
revenue, operating income, net income, EPS, operating cash flow, CAPEX, and FCF agree with the official
IR tables inspected in the pilot. This is a useful independent transformation cross-check, but the cache
metadata names Finnhub, SEC Company Facts, and Yahoo enrichment and does not preserve a per-row source
artifact hash or acceptance timestamp. It therefore remains a cross-check, not the primary v2.1 input.

The GOOGL `analystLog.oneTime` narrative correctly identifies very large 2026 unrealized investment
gains. The official Q2 release confirms $97.983 billion of other income and a $98.999 billion cash-flow
adjustment for gains on debt and equity securities. The narrative still cannot be converted into a
normalized EPS adjustment because it does not provide a uniform, source-bound after-tax per-share
calculation. The v2.1 P/E model remains blocked rather than inventing one.

## Point-in-time P/E conclusion

Usable historical P/E observations from the legacy project: **0**.

The aggregate band fields may be shown as legacy research context, but they cannot pass the formal
five-year / 20-observation gate. Reconstructing P/E requires dated, redistribution-permitted prices and
the GAAP TTM EPS vintage that was public on every price date. No interpolation or repetition of the
legacy aggregate values is permitted.

## Reusable parts and next minimum work

1. Preserve the legacy dashboard and cache hashes as lineage evidence.
2. Reuse the review signature idea, not the empty baseline state.
3. Convert `analystLog.oneTime` only when amount, tax basis, recurrence, source, and availability are all explicit.
4. Do not reuse `bandTable` as either raw history or a final buy zone.
5. Build a validated trailing-twelve-month bridge from official quarterly reports before treating a new
   quarter as fresh input to the annual DCF model.
6. Acquire a redistribution-safe dated price series before attempting the first formal P/E history.

No paid service, SEC access workaround, main merge, Streamlit deployment, or short-term-signal branch
change is part of this audit.
