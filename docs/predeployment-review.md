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

## 2026-10-13 follow-up

The workflow page still showed `0 workflow runs`; no `schedule` event has been observed. Repository
Actions are enabled, the default token setting is read-only, and the refresh job alone requests
`contents: write`. No classic branch protection or ruleset blocks an Actions push. These facts rule
out the visible permission and branch-rule causes, but do not establish why GitHub did not enqueue
the scheduled event. GitHub documents that scheduled events run only from the default branch and
may be delayed or dropped under load. A manual dispatch would not prove scheduled execution.

Manual refresh was not run because it can commit a new Yahoo/Nasdaq-derived snapshot to `main` and
thereby publish it through the existing Streamlit deployment while public-display rights remain
unconfirmed. The immutable recovery baseline is `main` commit
`4e7c1f10b065831abaacecd221742713bcc155e1`; its data blobs are:

- `data/universe.csv`: `eb7b4ef3fdb54e9bacaf9640d9126929939f7718`
- `data/status.json`: `f799732ae7fab8ff6f20dff31b748e8fd03e19e5`

If a future approved refresh publishes bad data, create a recovery branch from the then-current
`main`, restore only those two paths from the last verified data commit, validate them, and submit a
recovery pull request. Do not reset or force-push `main`.

PR validation is now separated into: (A) stored-snapshot integrity, which is independent of today's
date; (B) live operational freshness, which requires the latest completed XNYS session; and (C) a
fail-closed deployment gate requiring both current data and confirmed public-data rights. Scheduled
and manually dispatched refresh logs record `github.event_name` so a manual success cannot be
reported as a scheduled success.

Because Nasdaq's published legal notice does not grant automated extraction for this use, the PR
freshness job no longer performs an automatic Yahoo/Nasdaq collection. It reassesses the committed
per-security timestamps against the current XNYS session. A provider call is not evidence of a data
license, and a technically successful collection must not silently expand the permitted operating
scope.
