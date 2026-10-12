# ADR-0002: Point-in-time financial data and deterministic valuation engine

- Status: Accepted for Draft PR validation
- Date: 2026-10-12
- Model version: `valuation-v2.0.0`

## Context

StockLens must calculate reproducible fair-value ranges without user-adjustable EPS, multiples,
discount rates, safety margins, or model weights. A public free-tier deployment also cannot depend
on a paid model, hidden prompt, or a data license that prevents redistribution. Point-in-time
availability is required so later backtests cannot use a filing before the market could see it.

## Decision

1. Historical GAAP facts come from the official SEC EDGAR `companyfacts` API. Filing metadata and
   `acceptanceDateTime` come from the SEC `submissions` API. Both APIs require no key. The collector
   uses a repository URL and contact mailbox in its User-Agent, stays at or below 2.5 requests per
   second, retries only bounded transient failures, stops immediately on HTTP 403, and never logs
   response bodies, cookies, authorization headers, or request secrets.
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
5. The engine is deterministic and versioned. It selects from normalized EPS, five-year FCF DCF,
   EV/EBITDA, and a financial-sector residual P/B model. A REIT without verified FFO/AFFO is held
   rather than valued with manufacturing-company cash-flow rules.
6. Model weights are fixed by disclosed issuer family and renormalized only across models whose
   required inputs pass validation. `AMAT` uses cyclical normalization, `AVGO` emphasizes FCF because
   acquisition accounting can distort GAAP EPS, and `GOOGL` checks operating-income-derived core EPS
   when non-operating gains materially change EPS. `QCOM` uses the general multi-model path and its
   handset/licensing concentration is disclosed as a risk.
7. The current stock price is excluded from every fair-value calculation. It is used only after the
   intrinsic value is calculated to derive upside/downside and the final label.
8. Safety margin is system-generated from data coverage, model count, cross-validation state, and a
   cyclical-company premium. Missing data is never replaced with a market multiple or a made-up
   normal value.
9. Analyst consensus and revision trends remain `unavailable` until a free, redistribution-safe,
   point-in-time source is approved. Yahoo-derived financial statements and estimates are not stored
   or redistributed by this stage because their public consumer terms are not a suitable data license
   for the project.
10. SEC internal consistency (`net income / diluted shares` versus reported diluted EPS) is checked,
    but this is not represented as an independent financial-source cross-check. Confidence remains
    lower until such a source is configured.
11. Financial collection is provider-adapted. The default adapter is the per-company SEC API; an
    optional `SecBulkZipProvider` reads the official nightly `companyfacts.zip` and `submissions.zip`
    supplied by a separately approved download/cache step. The adapter never converts an API 403
    into an automatic bulk download because the archives are large and may be blocked by the same
    policy layer.
12. A failed refresh may retain a prior verified snapshot only as `stale`. A complete outage does
    not overwrite valuation or financial CSVs. Partial stale rows are labelled `판단 보류`, and the
    run records `eligible_for_new_signals=false`.

## Provider review (2026-10-12)

| Candidate | Access / coverage | Redistribution finding | Decision |
| --- | --- | --- | --- |
| SEC EDGAR APIs | Official US-GAAP/IFRS taxonomy facts and acceptance metadata; no key | SEC says sec.gov public information may be copied or further distributed with attribution | Primary, subject to fair-access policy |
| SEC nightly bulk ZIPs | Official full Company Facts and Submissions archives; republished nightly | Same SEC public-information policy | Supported as an operator-supplied bulk adapter; not auto-downloaded in PR CI |
| Issuer investor-relations filings | Official annual/quarterly reports, but formats and site automation rules differ | Public documents are useful for manual cross-check; site-specific automated reuse terms require per-issuer review | Cross-check candidate, not a general adapter yet |
| SimFin free/basic | Structured statements and publish/restatement dates | Free license restricts data to personal research and prohibits sharing/distribution | Rejected for the public dashboard |
| Yahoo/Nasdaq consumer financial endpoints | Technically broad but unofficial/consumer-facing for this use | No redistribution-safe license was established for stored fundamental statements | Rejected as a financial-statement source |

The 2026-10-12 local probe returned HTTP 403 from `data.sec.gov` Company Facts and Submissions and
from the official `www.sec.gov` Company Facts bulk archive. DNS and TLS completed; the response was
served by `AkamaiGHost`, with no `Retry-After` header. The same four-company PR job produced zero
financial periods. These results are access diagnostics, not evidence that the SEC APIs are globally
unavailable.

Official references:

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC developer/fair-access policy: https://www.sec.gov/about/developer-resources
- SEC public-information redistribution policy: https://www.sec.gov/about/privacy-information
- SimFin data license (rejected): https://www.simfin.com/en/commercial-license/
- U.S. Treasury daily rate feed: https://home.treasury.gov/treasury-daily-interest-rate-xml-feed

## Consequences

- Results are reproducible from committed inputs and a model version, and can be reconstructed as of
  a past market timestamp.
- U.S. GAAP domestic issuers have the best coverage. IFRS ADRs, companies with ambiguous XBRL tags,
  short fiscal transition years, and REITs without verified FFO/AFFO are expected to be held.
- The scheduled job makes roughly two SEC requests per mapped issuer and is deliberately rate-limited;
  it remains within the existing 15-minute GitHub Actions job under normal network conditions.
- A total provider outage preserves the last valid valuation snapshot, marks financial freshness
  stale, disables new signals, and records a degraded refresh attempt instead of overwriting good values.
- Forecast consensus, guidance text extraction, segment-level validation, and an independent licensed
  financial data cross-check are explicit follow-up items, not silently substituted.

