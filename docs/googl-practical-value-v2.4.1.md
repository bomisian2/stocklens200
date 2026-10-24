# GOOGL v2.4.1 practical reference-value audit

## 1. Decision

A limited B-grade per-share research range is mechanically calculable, but an A-grade formal fair value remains unavailable.
The B result may be shown only with its full assumptions, endpoint construction, source-integrity limitation and `hold` status.
It is not a confidence interval, target price, or buy/sell signal.

| Level | Outcome |
|---|---|
| A — formal value | unavailable |
| B — research reference | $41.63–$84.88 per share, no midpoint selected |
| C — no calculation | not selected because a transparent mechanical B envelope can be reproduced |
| Operational eligibility | false / hold |

The range combines the v2.3 reference research EV range of $647.648B–$757.346B with same-date official balances and two
mutually exclusive preferred-share branches. It deliberately recognizes cash and investment assets from 0% to 100% of their
reported carrying balances because no evidence supports one privileged haircut. This makes the range wide by design.

## 2. Official source integrity

The official filing index fixes the following identity and timing:

- accession: `0001652044-26-000071`;
- report period: 2026-06-30;
- filed: 2026-07-23;
- accepted: 2026-07-22 21:15:54 ET / 2026-07-23 01:15:54 UTC;
- SEC-reported primary-document size: 2,464,133 bytes.

A single fair-access request from the local Windows environment returned HTTP 403. It was not retried, proxied, or bypassed;
no response body was stored. Consequently, the raw 10-Q file and its local SHA-256 are still unavailable. The URL, accession,
acceptance time and document size are fixed, but this source is not promoted to an automated operating input. The already
stored Alphabet Q2 earnings-release PDF remains separately hash-verified.

## 3. Research bridge branches

Both branches use official balances dated 2026-06-30. Operating leases are not separately subtracted because the research
FCFF begins with unadjusted GAAP EBIT. Finance leases remain disclosed but excluded pending FCFF reconciliation.

### Branch 1 — preferred as claim

- EV: $647.648B–$757.346B;
- cash and investment recognition: $0–$388.061B;
- fixed claims: $100.164B debt + $7.100B NCI proxy + $19.000B preferred liquidation preference;
- denominator: 12.230B issued shares to 12.524B issued-plus-all-awards mechanical bound;
- research per-share envelope: **$41.63–$83.33**.

### Branch 2 — preferred if converted

- EV and investment recognition: same as branch 1;
- fixed claims: $100.164B debt + $7.100B NCI proxy;
- preferred claim is not subtracted;
- denominator: 12.230B issued shares to 12.578B including all awards and maximum preferred conversion ratio;
- capped-call and treasury-stock offsets remain unresolved;
- research per-share envelope: **$42.96–$84.88**.

The displayed combined range is the outer envelope, **$41.63–$84.88**. No midpoint, probability, safety margin, undervaluation
percentage or recommendation is generated.

## 4. Value-driver ranking

The table compares mechanical per-share spans on a common 12.404B-share comparison denominator. Diagnostic slices are held
constant within each row; they are not official forecasts or selected valuation defaults.

| Rank | Driver | Mechanical span | Evidence boundary |
|---:|---|---:|---|
| 1 | WACC | $63.36/share | 9%–15% v2.3 research grid at 2% terminal growth; formal beta/WACC missing |
| 2 | Five-year revenue growth and operating margin | $40.36/share | existing v2.3 paths at 13.5% WACC and 1.5% terminal growth |
| 3 | Investment-asset economic recognition | $31.29/share | 0%–100% of $388.061B reported carrying balances |
| 4 | Post-2026 CAPEX and reinvestment | $17.34/share | official 2026 guide plus existing v2.3 post-2026 paths |
| 5 | Financial debt and preferred claim | $9.61/share | carrying debt plus preferred liquidation preference |
| 6 | Dilution bound | $1.74/share | issued shares through all-awards/max-conversion mechanical bound |

The largest three uncertainties are therefore WACC, the five-year growth/margin path, and investment-asset economic value.
This ranking does not say those ranges are equally likely or empirically calibrated. It identifies where better evidence would
reduce the widest amount of output variation.

## 5. Practical improvement over v2.4

v2.4 correctly blocked all per-share arithmetic when the bridge was incomplete. v2.4.1 preserves that formal gate while
adding a separate research contract:

- every endpoint has a basis and rationale;
- no missing item is represented as an observed zero;
- investment recognition is a full sensitivity bound rather than an invented haircut;
- preferred claim and conversion branches remain mutually exclusive;
- share-count bounds are labelled mechanical, not fully diluted facts;
- no midpoint or probability is selected;
- the output cannot enter scheduled valuation files or trading signals.

This turns an undifferentiated `unavailable` result into a useful map of plausible arithmetic without overstating accuracy.

## 6. Minimum user display

A future research-only screen should show only:

1. `B — research reference / not an investment signal` badge;
2. financial statement date and market-price date separately;
3. $41.63–$84.88 research envelope with `no midpoint selected`;
4. claim-versus-if-converted branch assumptions;
5. the top three drivers and mechanical per-share spans;
6. source status: official accession/timestamp verified, raw-file hash unavailable due HTTP 403;
7. formal blockers and `operational eligibility: false / hold`.

No user control for EPS, PER, WACC, growth, discount rate or safety margin is added.

## 7. Common 200-company procedure

1. Import same-date issuer balance-sheet and claim facts with URL, accession/publication time, period, units and raw hash when
   an allowed source file is available.
2. Classify cash/investments, debt, leases, NCI, preferred, pensions, legal claims and contingent claims using a versioned
   sector policy.
3. Run lease, preferred, NCI and dilution double-count guards.
4. Produce A only with a formal operating forecast, WACC, bridge and point-in-time fully diluted denominator. Otherwise show
   a fully disclosed B envelope or C.

Exceptions:

- banks and insurers require equity-based models rather than a generic FCFF EV bridge;
- REITs require FFO/AFFO and property/debt conventions;
- convertibles, pensions, material litigation and cross-holdings need issuer-specific claim modules;
- IFRS ADRs need taxonomy and accounting-policy mapping.

## 8. Decision after this stage

### Option 1 — limited research display first (recommended)

Add a non-operational GOOGL research panel showing the B range, assumptions, driver ranking and hold status. Keep it outside
formal valuation CSVs, signals and scheduled publication. This tests whether users understand the uncertainty presentation
before investing more time in a perfect bridge.

### Option 2 — continue GOOGL design only

Do not expose the B result until an official raw source is hashable and the point-in-time dilution/capped-call policy is
complete. This maximizes provenance but postpones all user feedback.

The recommendation is option 1 only as a separately approved research preview. It is not authorization to merge, deploy or
turn the result into an investment decision.

## 9. Reproduction

```powershell
python scripts/audit_googl_practical_value.py `
  --bridge-evidence docs/evidence/googl-equity-bridge-v2.4.json `
  --scenario-evidence docs/evidence/googl-scenarios-v2.3.json `
  --universe data/universe.csv `
  --output <isolated-research-output.json>
```

## Sources

- SEC filing index: https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/0001652044-26-000071-index.htm
- Alphabet Q2 2026 Form 10-Q: https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm
- Alphabet Q2 2026 earnings release: https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf

