# GOOGL v2.4 enterprise-to-common-equity bridge audit

## 1. Outcome

The audit does not create a new valuation scenario. It tests whether the existing v2.3 research enterprise value can be
converted into common-equity and per-share value without silent assumptions.

| Output level | State | Result |
|---|---|---|
| A: verified value range | unavailable | formal WACC, forecast support, bridge policy and denominator remain incomplete |
| B: research reference | available at enterprise-value level only | non-operational v2.3 sensitivity is preserved |
| C: per-share value unavailable | active | no point-in-time fully diluted denominator and no complete equity bridge |

Formal fair value remains 0/5 and the operational decision remains `hold`. No Streamlit or scheduled production path reads
this audit.

## 2. Official source boundary

The balance-sheet and claim facts below were checked against Alphabet's official Q2 2026 Form 10-Q for the period ended
June 30, 2026. The filing URL and accession are recorded, but a local source-file hash was not captured. It is therefore an
official filing reference for this research audit, not an automated operating input. The existing Q2 earnings-release PDF
remains separately hash-verified.

All amounts are USD billions. `Official` means the amount is reported; it does not mean the amount is automatically eligible
for the valuation bridge.

## 3. Assets and claims

| Item | Amount | Evidence | Current bridge treatment | Reason |
|---|---:|---|---|---|
| Cash and cash equivalents | 55.911 | official | unresolved | minimum operating cash is not separated |
| Marketable securities | 186.563 | official | unresolved | mixed equity/debt, restrictions, tax and liquidity |
| Other non-current marketable equity | 14.126 | official | unresolved | sale restrictions extend through Q3 2027 |
| Non-marketable securities | 131.461 | book-value proxy | unresolved | economic value and operating/strategic classification missing |
| Interest-bearing debt | 100.164 | book-value proxy | research subtraction only | $1.999B current + $98.165B long-term carrying amount; market-value policy incomplete |
| Operating lease liabilities | 18.037 | official | unresolved | subtracting from GAAP-EBIT FCFF would double count without lease recast |
| Finance lease liabilities | 2.590 | official | unresolved | leased assets, depreciation and principal need reconciliation |
| Noncontrolling interests | 7.100 | book-value proxy | unresolved | economic claim value and consolidated FCFF ownership need reconciliation |
| Mandatory convertible preferred | 19.000 liquidation preference | official | unresolved | claim subtraction versus if-converted dilution and capped calls |
| Accrued fines and settlements | 17.356 | official | disclosure only | runoff cash flow may already belong in a forecast |
| Unconsolidated VIE funding commitments | 21.900 | official | disclosure only | contingent, not a recorded debt balance |

The marketable securities balance includes marketable equity securities, government bonds, corporate debt and
MBS/ABS. The non-marketable carrying value includes private securities whose liquidity and tax outcome are not equivalent
to cash. Adding the gross balances as "net cash" would overstate precision.

## 4. Investment gains and double counting

Investment gains are reported in other income and expense, outside operating income. The v2.3 FCFF research path begins
with operating income, so the gains themselves are not in NOPAT. A separately classified non-operating investment asset
could eventually enter the equity bridge without mechanically duplicating operating FCFF. That conclusion does not justify
adding every investment at carrying value: restrictions, tax leakage, liquidity, strategic use and operating cash still need
explicit treatment.

## 5. Share denominator

Alphabet reported 12.230 billion common shares issued and outstanding at June 30, 2026 and 12.274 billion H1 diluted
weighted-average shares. Neither is a point-in-time fully diluted denominator. Approximately 294 million unvested RSUs and
PSUs require a treasury-stock-method calculation. The mandatory convertible preferred can contribute dilution, while capped
calls may offset part of it. The bridge must either subtract the preferred claim and exclude conversion shares, or assume
conversion and include net dilution; doing both is rejected.

For scale only, a $10 billion balance change equals about $0.818 per 12.230 billion issued shares. This is neither diluted
per-share value nor fair value and is not used in a recommendation.

## 6. Input realism and sensitivity

The highest-value missing decisions are classification decisions, not new scenarios:

- how much cash is required for operations;
- what portion of investments is non-operating and realizable after restrictions and taxes;
- whether leases are capitalized in both the operating forecast and the bridge;
- whether NCI is valued at carrying amount or an economic value consistent with consolidated FCFF;
- whether mandatory preferred is a claim or conversion dilution;
- how awards, options, preferred conversion and capped calls produce a point-in-time fully diluted denominator.

Every $10 billion unresolved asset or claim changes common equity one-for-one before tax and liquidity effects. The same
amount changes the issued-share scale by about $0.818, demonstrating material sensitivity without pretending that the
issued-share count is a valid denominator. The much larger investment balances therefore dominate the bridge uncertainty.

## 7. Formal value blocker

A reference per-share value is not calculated. The blocking conditions are concrete:

1. operating cash has not been separated from excess cash;
2. investment balances lack tax, liquidity and operating/non-operating policy;
3. lease treatment is not consistent with the current FCFF definition;
4. NCI and preferred economic treatment are unresolved;
5. point-in-time fully diluted shares are unavailable;
6. the upstream v2.3 EV remains a research sensitivity without a formal WACC or validated forecast.

These are not represented as zero. The A/B/C gate prevents a B-grade EV from being promoted to an A-grade price target.

## 8. Two-framework analysis

This review combines **problem redefinition** and the **complexity resolution matrix**. The original question appears to be
"what is GOOGL worth per share?" Reframed one level higher, the real question is "which claims and assets stand between an
operating-enterprise value and the claim owned by one fully diluted common share, and can every transformation be reproduced
without incompatible accounting?" That rotation prevents a plausible DCF number from hiding an incomplete ownership map.

The complexity matrix then decomposes the bridge into operating cash, investment assets, financial debt, leases, NCI,
preferred claims, contingent claims and dilution. Interactions matter more than isolated data completion: lease liabilities
interact with EBIT and FCFF; preferred claims interact with the share denominator; investment gains interact with both NOPAT
normalization and asset addition. The leverage point is therefore a small set of accounting policies backed by official facts,
not another family of growth scenarios. On this basis, the correct present output is useful evidence plus a C-grade per-share
gate, rather than a falsely precise price.

## 9. Twelve implementation insights

1. **Reported does not mean bridge-ready.** A verified balance can still lack an economically consistent add/subtract rule.
2. **The denominator is part of valuation, not formatting.** Period-average diluted shares answer EPS, not point-in-time
   ownership value.
3. **Convertible preferred creates a fork.** Claim treatment and if-converted treatment are alternatives; combining them
   double counts the same security.
4. **Lease consistency outranks convention.** Whether leases are debt-like matters less than applying the same convention to
   operating profit, reinvestment, WACC and the bridge.
5. **Gross investments are not automatically excess cash.** Restriction, tax, liquidity and strategic use can materially
   separate carrying value from distributable value.
6. **Investment-income normalization and asset value are different layers.** Removing a gain from operating earnings does not
   prove the underlying asset's realizable common-equity value.
7. **Contingent commitments belong once.** They should appear in forecast cash flows or as a separate supported claim, not in
   both places.
8. **Book NCI is a proxy, not a market quote.** It can document a claim exists without claiming its economic value is known.
9. **A one-for-one sensitivity is more honest than a partial bridge.** It shows materiality without turning incomplete inputs
   into a pseudo-target price.
10. **A/B/C grading should apply at each layer.** Enterprise value can be B while common equity and per-share outputs are C.
11. **The 200-company bottleneck is semantic.** Collecting balance-sheet rows is easier than classifying cash, investments,
    leases, pensions, NCI and convertibles consistently across sectors and issuers.
12. **The next milestone is one complete claim map.** A reproducible GOOGL bridge is more valuable than 200 mechanically
    netted but accounting-inconsistent outputs.

## 10. Minimum data contract for 200 companies

- dated cash, investment and debt balances on a common valuation date;
- operating-cash and non-operating-asset classification;
- investment liquidity, restriction and tax treatment;
- lease convention matched to EBIT, FCFF and WACC;
- NCI, preferred, pension, legal and contingent-claim policy;
- point-in-time fully diluted shares with award, option and convertible treatment;
- formal WACC and source-supported multi-year operating forecasts;
- original source URL, publication time, unit, period, extraction provenance and file hash where imported.

Financial institutions and REITs need different bridges; this manufacturing/service-style EV bridge cannot simply be copied
to them.

## 11. Three next tasks

1. Import and hash official filing/IR notes into a reusable bridge-fact schema rather than relying on a manually inspected URL.
2. Validate one consistent lease, preferred and point-in-time dilution policy, including capped-call effects.
3. Establish operating-cash and investment tax/liquidity policy, then rerun the A/B/C gate before expanding to another issuer.

## 12. Reproduction

```powershell
python scripts/audit_googl_equity_bridge.py `
  --evidence docs/evidence/googl-equity-bridge-v2.4.json `
  --scenario-evidence docs/evidence/googl-scenarios-v2.3.json `
  --universe data/universe.csv `
  --output <isolated-research-output.json>
```

The script is offline and deterministic. It does not call SEC, a paid API or an AI model, does not alter production data and
does not generate a trading signal.

## Sources

- Alphabet Q2 2026 Form 10-Q: https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm
- Alphabet Q2 2026 earnings release: https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf

