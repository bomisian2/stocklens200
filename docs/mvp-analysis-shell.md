# StockLens 200 MVP analysis shell

## Scope

This branch adds a read-only, mobile-first analysis shell without importing the valuation or signal
engines from Draft PRs #2 and #3. The production market snapshot remains the only operational input.
Research summaries live under `research/`, are marked operationally ineligible, and cannot create a
formal valuation, ranking, alert, or trading signal.

## Pages

| Page | Purpose |
|---|---|
| 전체 종목 | Search, sector/state filters, safe sorting and all 200 market rows |
| 종목 상세 | Six independent analysis areas with source, date, state and hold reason |
| GOOGL 연구 | Isolated B-grade range, bridge branches, uncertainty and source limitations |
| 신호 검증 | Read-only PR #3 aggregate and LRCX insufficient-sample case |
| 데이터 상태 | System market health versus per-feature coverage and hold reasons |

The all-company table has two views so a 390-pixel phone does not need to show price and four
analysis states in the same wide grid. Neither view supports sorting by research value or signal
performance.

## Data contract

`stocklens.analysis.build_analysis_catalog` combines the verified `data/universe.csv` snapshot with
the small `research/mvp-analysis-overlays.json` evidence overlay. Every company receives six areas:

- `market`
- `business`
- `financial`
- `outlook`
- `valuation`
- `technical`

Each area has `state`, `as_of`, `summary`, `hold_reason`, `sources`, and `details`. Allowed states are
`verified`, `partial`, `research_only`, `stale`, `unavailable`, and `blocked`.

The loader rejects a research value if it is operationally eligible, contains a formal value or
midpoint, enables automatic price comparison, changes `hold`, or contains recommendation, target,
buy-zone, upside or undervaluation fields. The signal summary similarly requires zero independent
passes and zero active signals for the committed research snapshot.

## Pilot truth table

| Ticker | Market | Official financial periods | Value state | Operational decision |
|---|---|---:|---|---|
| GOOGL | verified | 11 | B-grade research only | false / hold |
| AMAT | verified | 0 | blocked | false / hold |
| AVGO | verified | 0 | blocked | false / hold |
| QCOM | verified | 0 | blocked | false / hold |

GOOGL's `$41.63-$84.88` envelope has no selected midpoint and is not compared with the current
market price. AMAT, AVGO and QCOM display their verified market rows and exact evidence gaps without
manufacturing financials or values.

## Reuse boundary

- PR #2: only reviewed result facts and the research-display contract are represented; valuation
  formulas, SEC clients and valuation CSV writers are not imported.
- PR #3: only its committed validation report is summarized; price history, strategy code and signal
  publisher are not imported.
- No raw Yahoo history, issuer PDF, SEC response body, secret or personal path is committed.

## Local verification

```powershell
python -m pytest -q
python scripts/validate_snapshot.py --strict
streamlit run app.py
```

The MVP must remain a Draft PR. Merging to `main` or deploying the existing Streamlit site requires a
separate approval.
