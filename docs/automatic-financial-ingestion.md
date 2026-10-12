# Automatic financial ingestion options

## Goal and constraints

The production target is unattended daily refresh for the current Top 200, without user-downloaded
files, access-control circumvention, unlicensed redistribution, paid services, or fabricated values.
The Streamlit process remains read-only and never contacts a financial-data provider.

## Options reviewed

| Option | Automation | Data authority and redistribution | Operational finding | Decision |
| --- | --- | --- | --- | --- |
| GitHub-hosted Actions calling SEC | Fully scheduled and free within current usage | Official SEC source; public information may be redistributed with attribution | Company Facts, Submissions, and bulk ZIP returned HTTP 403 in the observed runner | Not usable now; do not retry or route around the block |
| Operator-controlled self-hosted GitHub runner on a network where SEC allows normal access | Fully scheduled; no manual daily download | Official SEC source; normal fair-access policy applies | Technically suitable if the operator supplies and maintains the machine/network; not yet provisioned or tested | Recommended next deployment experiment, subject to explicit operator setup |
| Separate cloud scheduled job plus object storage | Fully scheduled | Official SEC source | Could work on an allowed egress IP, but free quotas, egress stability, storage retention, and provider account terms require a concrete platform review | Candidate only; no account or paid resource created |
| SEC quarterly Financial Statement Data Sets | Quarterly scheduled download | Official as-filed face-statement data | Smaller number of downloads, but quarterly latency and flattened TSV schema omit some Company Facts/Submissions metadata; parser not implemented | Useful bulk fallback/reference, not sufficient alone for daily filing freshness |
| Third-party free fundamentals API | Usually scheduled | Varies | Reviewed free tiers did not establish public-display/redistribution rights; some require keys or paid display licenses | Rejected until written rights and cost are approved |
| Issuer investor-relations sites | Potentially scheduled per issuer | Official issuer publications | Two hundred heterogeneous sites, document formats, robots rules, and amendment histories make a safe universal collector impractical; acceptance time and taxonomy are inconsistent | Manual/targeted cross-check only, not the primary Top-200 feed |

## Recommended production shape

```text
Operator-controlled scheduled runner on an SEC-permitted network
  -> official company_tickers.json
  -> official Company Facts + Submissions (identified User-Agent, <= 2.5 req/s)
  -> immutable local run directory + SHA-256 manifest
  -> CIK/ticker, filing time, amendment, fiscal-period and split checks
  -> deterministic normalized financial snapshot + provenance hash
  -> valuation quality/staleness gates
  -> commit only small validated normalized outputs to the data branch
  -> Streamlit reads stored output only
```

Raw archives should stay in a bounded, access-controlled cache owned by the operator rather than in
the Git repository. Each normalized snapshot records the raw artifact hashes and official URLs so it
can be traced to the exact imported files. A failed or partial run never replaces the last verified
snapshot. Files older than the financial freshness threshold remain displayable only as stale and
are ineligible for new valuation or trading signals.

## Preconditions before enabling a scheduled collector

1. Provision an operator-controlled runner and verify that ordinary SEC access succeeds without a
   proxy, IP rotation, CAPTCHA, or other bypass.
2. Keep the identifying User-Agent and request rate within SEC fair-access guidance.
3. Complete a cacheless four-company run, then verify hashes, CIK/ticker identity, acceptance times,
   amendments, fiscal years, split-adjusted comparisons, and normalized figures against filings.
4. Expand only after passing gates: 4 companies, then 20, 50, and 200.
5. Measure daily runtime, raw-cache growth, and GitHub/hosting usage before enabling a schedule.
6. Keep the direct API adapter disabled in environments that return HTTP 403.

No self-hosted runner or alternate cloud account has been provisioned by this PR. Consequently the
recommended architecture is implementable but not yet an operating free automatic source.
