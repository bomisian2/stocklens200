# Same-Date P4.3 missing evidence plan

This plan is offline-only. It does not authorize collection, purchase, public
display, parser promotion, a v1.1 snapshot, or an investment decision.

| Required evidence | Minimum contract | Preferred acquisition path | Current state / gate |
|---|---|---|---|
| NVDA daily price | Provider-native daily OHLCV, exchange date, capture time, source hash | Reassess Alpha Vantage only after a new explicit call approval, or evaluate a licensed personal-use market-data vendor | Missing. The preserved Alpha response is an invalid provider dataset classified as `provider_rate_or_plan_limit`; quota exhaustion versus plan entitlement is undetermined. |
| AVGO/NVDA PIT Forward EPS | Per-vintage estimate, ticker, FY1/NTM horizon, currency, per-share unit, accounting basis, analyst count, `as_of`, `available_at`, source hash | Licensed consensus provider with explicit local-storage and derived-analysis rights | Missing. SEC and issuer filings are reported actuals and cannot substitute for Street consensus. |
| GAAP vs non-GAAP basis | Explicit provider field or provider methodology tied to each estimate vintage | Same licensed consensus source plus methodology document | Missing. Unknown basis remains blocked and is never inferred from the number. |
| FY1/NTM definition | Fiscal calendar, horizon definition, roll date and period end | Same licensed consensus source | Missing. FY1 and NTM are not interchangeable. |
| Availability/vintage | Original provider timestamp and immutable capture timestamp not later than decision time | Provider-native vintage feed or immutable dated export | Missing for forward estimates. A current snapshot cannot recreate an earlier vintage. |
| QQQ/SOXX comparables | Same-basis constituent estimates, composition, weights, price time and calculation version | Licensed ETF/constituent and consensus sources | Missing. Cross-project normalized output is not independent provider evidence. |
| Adjusted daily prices | Corporate-action policy, split/dividend factors, adjusted close and trading calendar | Licensed historical-price source whose terms permit local caching and derived research | Missing. The captured AVGO compact series is 100 rows of unadjusted OHLCV only. |
| Usage rights | Local storage, derived analysis, local display, public display, redistribution, retention/deletion and attribution | Independent review of the captured provider terms; obtain written clarification where clauses are silent | Pending. Public display and redistribution stay `not_authorized`; other data-use scopes require independent review. |

## AVGO captured daily data restrictions

- Scope: 100 compact daily rows, 2026-05-26 through 2026-10-15.
- Basis: as-traded, unadjusted OHLCV.
- Allowed before rights review: evidence integrity inspection only.
- Prohibited promotion: adjusted returns, split/dividend-aware performance,
  long-horizon backtests, Forward PER ledgers, snapshots, valuation decisions,
  buy levels, or operational signals.

## Minimum next step

1. Independent QA reviews the private terms packet and records an approval or
   rejection for each usage scope.
2. Obtain a source that contractually supplies PIT Forward EPS and adjusted
   prices with local storage and derived-analysis rights.
3. Only after the source bytes, timestamps, basis, rights and hashes pass the
   existing validators may a parser candidate be considered.
