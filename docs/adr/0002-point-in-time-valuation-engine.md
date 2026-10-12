# ADR-0002: Point-in-time financial data and deterministic valuation engine

- Status: Accepted for Draft PR validation
- Date: 2026-10-12
- Model version: `valuation-v2.1.0`

## Context

StockLens must calculate reproducible fair-value ranges without user-adjustable EPS, multiples,
discount rates, safety margins, or model weights. A public free-tier deployment also cannot depend
on a paid model, hidden prompt, or a data license that prevents redistribution. Point-in-time
availability is required so later backtests cannot use a filing before the market could see it.

## Decision

1. Historical GAAP facts come only from official SEC EDGAR Company Facts data. Filing metadata and
   `acceptanceDateTime` come from official SEC Submissions data. The safe default is an offline,
   hash-verified manifest of operator-supplied official JSON or nightly ZIP files. Direct API access
   is an explicit opt-in adapter, never a fallback: it uses an identifying User-Agent, stays below
   2.5 requests per second, retries only bounded transient failures, stops immediately on HTTP 403,
   and never logs response bodies, cookies, authorization headers, or request secrets.
2. The 10-year risk-free rate comes from the U.S. Treasury Daily Par Yield Curve XML feed. The
   latest observation on or before the universe market date is selected.
3. Every normalized period stores fiscal year, original period dates, form, accession, filed date,
   market availability time, whether that time is estimated, its estimation basis, taxonomy, source
   URL, and validation state. An actual SEC acceptance timestamp always wins. If only `filed` exists,
   the fact is gated until 23:59:59 UTC on the following calendar day and explicitly marked
   `availability_estimated=true`; same-day 00:00 UTC is never assumed.
4. Annual duration facts must span 300–430 days. This rejects short transition periods during a
   fiscal-year change until a dedicated normalization rule is available. Later restatements and
   split-adjusted comparatives are used only when their availability time is not after the requested
   valuation time.
5. The engine is deterministic and versioned. The P/E model requires at least 20 clean observations
   spanning five years. Every observation combines the price on that date with EPS that was publicly
   available on that date. Negative EPS, observations flagged as distorted, and EPS published after
   the price date are excluded. The target multiple combines a rate-conditioned issuer distribution
   and an available sector-relative distribution; it never falls back to a fixed sector P/E.
6. GAAP EPS is distinct from normalized EPS. A normalized value is accepted only with dated source
   evidence and categorized adjustments for amortization, asset disposal, restructuring, stock
   compensation, impairment, investment gains/losses, litigation, or other disclosed items. Negative,
   structural-loss, or unverified EPS disables P/E. `INTC` is the explicit turnaround/structural-loss
   regression case. PEG is not implemented as a substitute.
7. EV/EBITDA requires at least five point-in-time comparable observations and never uses a fixed sector
   multiple. Available model weights are inverse to their scenario uncertainty instead of fixed by
   ticker family. Growth/ROIC can alter P/E only when a calibration was selected before an independent
   holdout with at least 30 observations and accepted; otherwise the coefficient is zero.
8. FCF growth is applied once inside forecast cash flows. The risk-free rate is applied through cost of
   capital. No separate fixed growth multiplier, VIX price multiplier, oil multiplier, or macro haircut
   is applied. VIX remains a technical-risk input outside intrinsic value. Oil sensitivity remains
   unavailable until issuer/industry evidence is supplied.
9. The current stock price is excluded from every fair-value calculation. It is used only after the
   intrinsic value is calculated to derive upside/downside and the final label.
10. Conservative/base/optimistic values come from P/E quartiles, comparable-multiple quartiles, DCF
   forecast dispersion, historical forecast error, model disagreement, leverage, and data quality.
   The safety margin and buy band are derived from those company-specific risks; no fixed 90-110%
   range is used. A dispersion-derived hysteresis buffer prevents a prior state from flipping on a
   small boundary crossing. Intrinsic value and technical entry timing remain independent.
11. Analyst consensus and revision trends remain `unavailable` until a free, redistribution-safe,
   point-in-time source is approved. Yahoo-derived financial statements and estimates are not stored
   or redistributed by this stage because their public consumer terms are not a suitable data license
   for the project.
12. SEC internal consistency (`net income / diluted shares` versus reported diluted EPS) is checked,
    but this is not represented as an independent financial-source cross-check. Confidence remains
    lower until such a source is configured.
13. Financial collection is provider-adapted. `SecFileProvider` reads individual official files or
    nightly `companyfacts.zip` and `submissions.zip` archives supplied by the operator. It verifies
    canonical SEC source URLs, local path containment, CIK, acquisition time, and SHA-256 before
    normalizing. Raw and normalized hashes and the normalization version are persisted. Operational
    manifests must declare `fixture=false`; test fixtures cannot silently enter an operational run.
14. A failed refresh may retain a prior verified snapshot only as `stale`. A complete outage does
    not overwrite valuation or financial CSVs. Partial stale rows are labelled `판단 보류`, and the
    run records `eligible_for_new_signals=false`.

## Provider review (2026-10-12)

| Candidate | Access / coverage | Redistribution finding | Decision |
| --- | --- | --- | --- |
| SEC EDGAR APIs | Official US-GAAP/IFRS taxonomy facts and acceptance metadata; no key | SEC says sec.gov public information may be copied or further distributed with attribution | Explicit opt-in only; not retried after the observed 403 |
| SEC nightly bulk ZIPs / individual JSON | Official full Company Facts and Submissions data; bulk files republished nightly | Same SEC public-information policy | Primary operator-supplied offline input; never auto-downloaded in PR CI |
| SEC quarterly Financial Statement Data Sets | Official, as-filed numeric face statements in quarterly flattened packages | Same SEC public-information policy | Approved for manual acquisition; this PR does not yet parse the TSV schema |
| Issuer investor-relations filings | Official annual/quarterly reports, but formats and site automation rules differ | Public documents are useful for manual cross-check; site-specific automated reuse terms require per-issuer review | Cross-check candidate, not a general adapter yet |
| SimFin free/basic | Structured statements and publish/restatement dates | Free license restricts data to personal research and prohibits sharing/distribution | Rejected for the public dashboard |
| Alpha Vantage free/demo | Endpoint reachable in a one-time probe | Personal, non-commercial use unless separately agreed | Rejected for public dashboard use |
| Twelve Data free/demo | Endpoint reachable in a one-time probe | External display/redistribution requires an add-on or separate agreement | Rejected |
| Financial Modeling Prep / Polygon no-key | Returned HTTP 401 in a one-time probe | No free public-display right was established | Rejected; no subscription started |
| Yahoo/Nasdaq consumer financial endpoints | Technically broad but unofficial/consumer-facing for this use | No redistribution-safe license was established for stored fundamental statements | Rejected as a financial-statement source |

The 2026-10-12 local probe returned HTTP 403 from `data.sec.gov` Company Facts and Submissions and
from the official `www.sec.gov` Company Facts bulk archive. DNS and TLS completed; the response was
served by `AkamaiGHost`, with no `Retry-After` header. The five-company gate (GOOGL, AMAT, AVGO,
QCOM, INTC) still has no verified operational evidence bundle and must not report live valuations.
These results are access diagnostics, not evidence that the SEC APIs are globally
unavailable.

Official references:

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC developer/fair-access policy: https://www.sec.gov/about/developer-resources
- SEC public-information redistribution policy: https://www.sec.gov/about/privacy-information
- SimFin data license (rejected): https://www.simfin.com/en/commercial-license/
- U.S. Treasury daily rate feed: https://home.treasury.gov/treasury-daily-interest-rate-xml-feed

The detailed access and licensing matrix is in `docs/financial-source-review.md`. Offline import
instructions and integrity gates are in `docs/sec-offline-import.md`. Automatic ingestion options
and the recommended self-hosted collector boundary are in `docs/automatic-financial-ingestion.md`.

## Consequences

- Results are reproducible from committed inputs and a model version, and can be reconstructed as of
  a past market timestamp.
- U.S. GAAP domestic issuers have the best coverage. IFRS ADRs, companies with ambiguous XBRL tags,
  short fiscal transition years, and REITs without verified FFO/AFFO are expected to be held.
- The scheduled job does not make SEC requests by default. It skips valuation refresh when no
  operator-supplied verified manifest is present, while the independent Top-200 refresh continues.
- A total provider outage preserves the last valid valuation snapshot, marks financial freshness
  stale, disables new signals, and records a degraded refresh attempt instead of overwriting good values.
- Forecast consensus, guidance text extraction, segment-level validation, and an independent licensed
  financial data cross-check are explicit follow-up items, not silently substituted.

