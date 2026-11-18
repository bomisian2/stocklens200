# Automatic financial ingestion options

## Goal and constraints

The production target is unattended daily refresh for the current Top 200, without user-downloaded
files, access-control circumvention, unlicensed redistribution, paid services, or fabricated values.
The Streamlit process remains read-only and never contacts a financial-data provider.

## Options reviewed

| Option | Automation | Data authority and redistribution | Operational finding | Decision |
| --- | --- | --- | --- | --- |
| GitHub-hosted Actions calling SEC | Fully scheduled and free within current usage | Official SEC source; public information may be redistributed with attribution | Company Facts, Submissions, and bulk ZIP returned HTTP 403 in the observed runner | Not usable now; do not retry or route around the block |
| Current Windows PC as a self-hosted runner | Fully scheduled if left online | Official SEC source; normal fair-access policy applies | Direct GOOGL Company Facts returned HTTP 403 on 2026-10-12; the one-shot script made no further requests | Rejected on the currently observed network; do not repeat or work around the block |
| Operator-controlled self-hosted GitHub runner on a different normal network where SEC allows access | Fully scheduled; GitHub does not charge Actions usage for self-hosted runners, but the operator pays for the machine/network | Official SEC source; normal fair-access policy applies | Technically suitable, but no alternate machine/network has been provisioned or tested | Preferred next experiment only if the operator already has a permitted machine; SEC success remains unverified |
| Google Cloud Run job + Cloud Scheduler | Fully scheduled; Cloud Run has monthly compute free allowances and Scheduler includes three jobs per billing account | Official SEC source; normal fair-access policy applies | A five-company probe is likely within the stated free allowances, but an account/billing setup is required, SEC access from its egress is unverified, outbound transfer has separate limits, and a daily Top-200 full download may exceed free transfer/storage | Validation candidate only after explicit account setup approval; do not describe as free production until measured |
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
3. Complete a cacheless five-company run, then verify hashes, CIK/ticker identity, acceptance times,
   amendments, fiscal years, split-adjusted comparisons, and normalized figures against filings.
4. Expand only after passing gates: 4 companies, then 20, 50, and 200.
5. Measure daily runtime, raw-cache growth, and GitHub/hosting usage before enabling a schedule.
6. Keep the direct API adapter disabled in environments that return HTTP 403.

No alternate self-hosted runner or cloud account has been provisioned by this PR. Consequently the
recommended architecture is implementable but not yet an operating free automatic source. Creating
a cloud project, enabling billing, or provisioning a runner is an external account/resource change
and requires separate approval.

## Current free-operation comparison

The comparison was refreshed on 2026-10-12 from official documentation:

- SEC asks clients to download only what they need, identify automated tools, and remain below an
  aggregate 10 requests per second:
  https://www.sec.gov/about/developer-resources
- GitHub documents self-hosted Actions runners as free to use with Actions while the operator is
  responsible for machine maintenance and cost:
  https://docs.github.com/en/actions/concepts/runners/self-hosted-runners
- Google Cloud documents Cloud Run free compute allowances, but also separate outbound data-transfer
  conditions:
  https://cloud.google.com/run/pricing
- Google Cloud Scheduler documents three jobs per billing account per month as free:
  https://cloud.google.com/scheduler/pricing
- Scheduling a Cloud Run job requires Cloud Scheduler and IAM configuration:
  https://cloud.google.com/run/docs/execute/jobs-on-schedule

These pricing pages do not prove that SEC will accept a given cloud egress IP. A candidate is marked
usable only after the exact one-shot script succeeds without a proxy, alternate hostname, repeated
403 requests, or other bypass. A free allowance is also not a guarantee that 200-company daily raw
downloads remain at zero cost; measured transfer, storage, build, and scheduler usage must all stay
inside their respective allowances.
