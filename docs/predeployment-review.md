# MVP predeployment review

## Baseline and rollback

- Review branch: `feat/v2-mvp-analysis-shell`
- Review head before final review fixes: `080d1676c2745e4930c16cbc4b908654bcaf7aa0`
- `main` baseline at review start: `4e7c1f10b065831abaacecd221742713bcc155e1`
- PRs #2 and #3 remain unmerged Draft pull requests.

Recheck `origin/main` immediately before any approved merge. If the deployed merge must be rolled
back, create a dedicated rollback branch from the then-current `main`, run
`git revert -m 1 <PR-4-merge-commit>`, verify tests and the stored snapshot, and submit the revert as
a pull request. Do not reset or force-push `main`. Streamlit Community Cloud should then redeploy the
revert commit. Snapshot commits made after the merge must be reviewed separately rather than erased.

## Freshness finding

At 2026-10-12 22:48 UTC, the latest completed XNYS session was 2026-10-12 while the committed
snapshot represented 2026-10-09. The persisted snapshot remained structurally healthy and had been
verified when collected, but it was stale at the review time. The MVP now computes runtime freshness
separately and disables signal eligibility for a stale snapshot.

## Scheduled collection finding

The `Refresh StockLens data` workflow had zero GitHub Actions runs when checked on 2026-10-12 at
approximately 22:50 UTC. Its first upcoming weekday schedule was 23:15 UTC. Therefore no production
scheduled collection success is claimed. A completed scheduled run must be inspected before merge.

## MVP and responsive verification

- The catalog contains 200 unique tickers. Search, price sorting and analysis-state filtering pass
  Streamlit AppTest coverage.
- GOOGL remains `research_only` with `operational_eligible=false`; AMAT, AVGO and QCOM show
  unavailable financial data and blocked valuation instead of generated values.
- The research contracts reject formal valuation fields, recommendations and active signals. PR #2
  valuation engines and PR #3 strategy/publishing code are not imported by the MVP.
- Browser checks at 390x844 and 1440x900 showed no page-level horizontal overflow. The compact
  mobile view keeps the stale warning, company count, freshness and market date ahead of the table.
- Local unit and Streamlit tests: 34 passed. The Windows sandbox emitted a temporary-directory
  cleanup warning after the successful test result; GitHub's Linux runner is the release gate.

## Data-use finding

The [yfinance project](https://github.com/ranaroussi/yfinance#readme) states that it is intended for
research and education and that Yahoo Finance API data is intended for personal use. [Yahoo's general
terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) grant personal, revocable use and
restrict creating substitute data services. [Nasdaq's website terms](https://www.nasdaq.com/legal)
limit use to personal, non-commercial use and prohibit copying or distributing its content without
written approval. The current public display of stored price, market-cap and metadata fields is not
covered by a confirmed redistribution grant.

Technical readiness does not resolve this legal/licensing uncertainty. Public deployment remains
blocked until written permission or a provider with explicit public-display and redistribution rights
is selected. This is an engineering compliance finding, not legal advice.
