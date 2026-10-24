# GOOGL FCFF v2.2 economic-definition audit

Audit date: 2026-10-12  
Financial evidence date: 2026-07-22  
Market session used only for reverse DCF: 2026-10-09  
Model version: `valuation-v2.2.0`

This is a research audit, not a formal fair value, price target, or investment recommendation. It preserves the existing
CFO-minus-CAPEX result and adds independent gates for NOPAT, FCFF, WACC, enterprise value and the common-equity
bridge. A missing component remains null; it is never filled with zero or an assumed industry value.

Machine-readable evidence is in [`evidence/googl-fcff-v2.2.json`](evidence/googl-fcff-v2.2.json). The file identifies
the official issuer PDFs, SHA-256 hashes, balance-sheet captions, one-time gain evidence, Treasury observation and every
known classification gap.

## 1. CFO-minus-CAPEX is not FCFF

The previous research model starts with:

`$185.675B operating cash flow - $132.402B CAPEX = $53.273B issuer-defined FCF`.

US-GAAP operating cash flow is after interest and tax. It also incorporates cash working-capital movements and removes
non-cash investment gains. Without a verified debt-financing adjustment it is neither FCFF nor canonical FCFE. Version
2.2 therefore keeps the label `levered_equity_cash_flow_proxy_not_canonical_fcfe`; no name-only promotion to FCFF occurs.

The formal operating model is:

`NOPAT = operating income × (1 - verified operating tax rate)`

`FCFF = NOPAT + D&A - CAPEX - increase in operating working capital`

Every term must use a consistent period and an operating/non-operating classification.

## 2. Tax and NOPAT evidence

| Input / diagnostic | TTM Q2 2026 |
|---|---:|
| Operating income | $147.628B |
| Consolidated pre-tax income | $299.269B |
| Consolidated income-tax provision | $55.064B |
| Reported consolidated effective rate | 18.3995% |
| Q1+Q2 identified equity-security gains | $135.946B |
| Issuer-stated after-tax net-income effect | $105.800B |
| Implied tax associated with identified gains | $30.146B |
| Event-adjusted consolidated pre-tax income | $163.323B |
| Event-adjusted consolidated tax | $24.918B |
| Event-adjusted total effective rate | 15.2569% |

The 18.3995% reported rate produces a **research** NOPAT proxy of $120.465B. Removing the identified Q1/Q2 gains and
their issuer-stated after-tax effects produces another research proxy of $125.105B. Neither is a verified operating tax
rate: the remaining consolidated result still contains other income, jurisdiction mix, credits and discrete tax items.
Formal NOPAT is therefore null.

## 3. Operating working capital

The official H1 releases provide the following reported captions:

| Component | 2025-06-30 | 2026-06-30 | Classification status |
|---|---:|---:|---|
| Accounts receivable | $55.048B | $69.175B | operating candidate |
| Inventory | not separately disclosed | $9.991B | missing comparable |
| Other current assets | $16.020B | $21.884B | mixed / unclassified |
| Accounts payable | $8.347B | $20.258B | operating candidate |
| Accrued compensation | $12.168B | $15.086B | operating candidate |
| Accrued expenses and other current liabilities | $52.039B | $73.014B | mixed / unclassified |
| Accrued revenue share | $9.787B | $10.599B | operating candidate |
| Deferred revenue | $4.969B | $7.154B | operating candidate |

Inventory was not separately disclosed in the 2025 comparative balance sheet. The two `other` captions can contain tax,
financing, acquisition and legal items as well as operating items. Total current assets less total current liabilities would
also mix cash and marketable securities into working capital. Consequently, formal TTM change in operating working capital
is null.

## 4. Partial FCFF diagnostic

| Component | Reported-rate proxy | Event-adjusted-rate proxy |
|---|---:|---:|
| NOPAT proxy | $120.465B | $125.105B |
| Add D&A | $25.237B | $25.237B |
| Subtract CAPEX | ($132.402B) | ($132.402B) |
| Pre-working-capital FCFF proxy | $13.300B | $17.940B |
| Operating working-capital change | unknown | unknown |
| **Formal FCFF** | **null** | **null** |

These partial values show where the economic cash flow would start; they are not valued, annualized, normalized or exposed
as an operational fair value.

## 5. WACC gate

The previous 11.74% discount rate was `5.24% Treasury + fixed 5.00% ERP + 1.50% data-quality premium`. This is retained
only as a legacy cost-of-equity proxy for reproducing the old calculation.

| WACC component | Availability | Decision |
|---|---|---|
| Risk-free rate 5.24% | verified Treasury observation | usable component |
| Beta | unavailable on a validated point-in-time basis | blocks WACC |
| Equity-risk premium 5.00% | prior fixed input; source/calibration unverified | blocks WACC |
| Pre-tax cost of debt | current debt yield / consistent interest cost unavailable | blocks WACC |
| Marginal operating tax rate | unavailable | blocks tax shield |
| Equity market value | observed price basis, no independent same-time price cross-check | research diagnostic only |
| Debt market value | unavailable | blocks capital weights |
| Data-quality premium 1.50% | uncertainty penalty, not economic business risk | excluded from formal WACC |

Formal WACC is null. Data uncertainty is reported as a confidence dimension rather than silently capitalized as company risk.

## 6. Enterprise value to common equity

Observed at 2026-06-30:

- Cash: $55.911B
- Marketable securities: $186.563B
- Non-marketable securities: $131.461B
- Reported long-term debt: $98.165B
- Non-current operating lease liabilities: $14.591B
- Mandatory-convertible preferred liquidation preference: $18.023B

The bridge is not executed because enterprise value is absent, current debt-like lease/financing claims are not isolated,
non-marketable securities are not classified as distributable non-operating assets, and current NCI/other potential claims
are not verified. No missing claim is assumed to be zero. The earlier $144.309B cash-plus-marketable-securities-minus-debt
diagnostic remains unapplied.

## 7. Growth, reinvestment and reverse DCF

The engine now supports the causal sequence:

`revenue -> operating margin -> EBIT -> operating tax -> NOPAT -> D&A/CAPEX/working capital -> FCFF`.

It rejects a positive-growth scenario with non-positive net reinvestment unless verified capacity or asset-light evidence is
supplied. For GOOGL, the official $175B-$185B 2026 CAPEX guide is available, but official multi-year revenue growth,
operating-margin, D&A and operating-working-capital forecasts are not. There are therefore **zero fully identified operating
scenario combinations**. The state remains `underdetermined`.

The old reverse DCF still reports the 59.02% implied five-year FCF CAGR at 11.74% / 2.5%, solely as a market-expectations
diagnostic. It is not translated into a forecast or target. Historical revenue growth and margins may be displayed as
comparators, but they do not fill missing forward assumptions.

The observed FY2022-FY2025 annual revenue-growth range is 8.68%-15.09%, and the annual operating-margin range is
26.46%-32.11%. Together with the official $175B-$185B one-year CAPEX guide these form comparison anchors only. Because
the forward tax, D&A, working-capital, multi-year CAPEX and WACC paths are absent, the number of complete source-supported
revenue/margin/reinvestment combinations is **0**.

## 8. Economic conclusion

The $64.44 research value remains arithmetically reproducible but economically incomplete:

1. it discounts an issuer-defined levered cash-flow proxy, not FCFF;
2. 11.74% is not a verified WACC;
3. there is no enterprise value or valid common-equity bridge;
4. TTM CAPEX is at a major investment inflection;
5. forecast revenue, margin, reinvestment and working capital are not verified;
6. terminal value is 68.13% of the old result.

The discrepancy from the market price triggers assumption diagnostics, not an automatic `overvalued` label.

| Output | Result |
|---|---|
| Formal NOPAT | unavailable |
| Formal FCFF | unavailable |
| Formal WACC | unavailable |
| FCFF enterprise value | unavailable |
| Formal common-equity value | unavailable |
| Formal per-share value | unavailable |
| Old research value | $64.44, preserved as provisional only |
| Operational judgement | hold |

## 9. Reproduction

```powershell
python scripts/audit_googl_dcf.py `
  --financials <normalized-financials.csv> `
  --universe <verified-universe.csv> `
  --risk-free-rate 0.0524 `
  --fcff-evidence docs/evidence/googl-fcff-v2.2.json `
  --output <isolated-audit-output.json>
```

The audit is offline and deterministic. It does not download data, call a paid API, alter production snapshots or make a
new investment signal.

## Official sources

- Alphabet FY2025/Q4 release: https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf
- Alphabet Q2 2025 release: https://s206.q4cdn.com/479360582/files/doc_financials/2025/q2/2025q2-alphabet-earnings-release.pdf
- Alphabet Q2 2026 release: https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf
- U.S. Treasury daily par yield curve: https://home.treasury.gov/treasury-daily-interest-rate-xml-feed

