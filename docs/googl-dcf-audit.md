# GOOGL DCF and reverse-DCF audit

Audit date: 2026-10-12  
Market session: 2026-10-09  
Market price: $351.66 (`yahoo_screener`, market time 2026-10-09T20:00:00Z)  
Model version: `valuation-v2.1.2`

This document audits a research calculation. It is not an operational fair value, price target, or investment
recommendation. The stored market session passed the universe timestamp gate, but the price itself has not been
independently cross-checked against a second redistribution-compatible price source.

## 1. Why the existing base value is $64.44

The model starts with Alphabet's issuer-defined free cash flow, CFO less purchases of property and equipment. US-GAAP
CFO is after interest and tax, so this is not FCFF. It resembles levered equity cash flow, but is not canonical FCFE because
debt issuance/repayment and a verified operating/non-operating asset split are unavailable. It is therefore labelled
`levered_equity_cash_flow_proxy_not_canonical_fcfe`.

| Input | Value | Basis |
|---|---:|---|
| TTM CFO | $185.675B | Official Q2 2026 release |
| TTM CAPEX | $132.402B | Official Q2 2026 release |
| Starting FCF | $53.273B | CFO minus CAPEX |
| Diluted shares | 12.274B | H1 2026 weighted diluted shares; denominator limitation retained |
| Five-year growth | 9.6954% | Median of 2022-2025 revenue CAGR 12.5117% and FCF CAGR 6.8791% |
| Discount rate | 11.74% | 5.24% Treasury + 5.00% ERP + 1.50% data-quality premium |
| Terminal growth | 2.50% | Existing capped rule |

Growth is applied once to FCF. Revenue growth is not multiplied into cash flow a second time.

| Year | Projected FCF | Discount factor | Present value |
|---:|---:|---:|---:|
| 1 | $58.438B | 0.894935 | $52.298B |
| 2 | $64.104B | 0.800908 | $51.341B |
| 3 | $70.319B | 0.716760 | $50.402B |
| 4 | $77.137B | 0.641454 | $49.480B |
| 5 | $84.616B | 0.574059 | $48.574B |

- Explicit-period present value: $252.095B
- Terminal value at year 5: $938.646B
- Present value of terminal value: $538.839B
- Total equity-cash-flow present value: $790.934B
- Terminal-value share: 68.13%
- Per-share result: $790.934B / 12.274B = **$64.44**

There is no enterprise value in this calculation. Cash, marketable securities and debt are not bridged into the result.
At Q2 2026 the observed balance-sheet amounts are cash $55.911B, marketable securities $186.563B and debt $98.165B,
or net financial assets of $144.309B. Adding that number to the present value would mix an FCFF bridge with a levered
cash-flow proxy and could double count returns already present in CFO. The model records the amount but does not apply it.

Conclusion: $64.44 is arithmetically reproducible under the stated rules, but it is not a fully specified FCFF or FCFE
valuation and cannot be promoted to an operational fair value.

## 2. CAPEX history and normalization limits

| Period | CAPEX | Revenue | CAPEX / revenue | D&A | CAPEX less D&A |
|---|---:|---:|---:|---:|---:|
| FY2022 | $31.485B | $282.836B | 11.13% | $13.475B | $18.010B |
| FY2023 | $32.251B | $307.394B | 10.49% | $11.946B | $20.305B |
| FY2024 | $52.535B | $350.018B | 15.01% | $15.311B | $37.224B |
| FY2025 | $91.447B | $402.836B | 22.70% | $21.136B | $70.311B |
| TTM Q2 2026 | $132.402B | $445.866B | 29.70% | $25.237B | $107.165B |

Alphabet raised its 2025 CAPEX expectation from $75B to approximately $85B in the official Q2 2025 call; actual 2025
CAPEX was $91.447B. The official FY2025/Q4 release then guided 2026 CAPEX to $175B-$185B. H1 2026 CAPEX was
$80.598B, so the guidance mechanically implies $94.402B-$104.402B of H2 investment.

The trend and guidance do not support treating TTM CAPEX as a temporary anomaly that can simply be added back. D&A is
not a verified maintenance-CAPEX estimate; using it as a floor is only an intentionally extreme sensitivity.

Mechanical scenarios hold TTM CFO constant and are not forecasts:

| CAPEX assumption | Mechanical FCF | Implied five-year FCF CAGR at 11.74% / 2.5% |
|---|---:|---:|
| Reported TTM $132.402B | $53.273B | 59.02% |
| FY2025 actual $91.447B | $94.228B | 40.81% |
| FY2026 guide low $175B | $10.675B | 122.07% |
| FY2026 guide midpoint $180B | $5.675B | 152.71% |
| FY2026 guide high $185B | $0.675B | 289.08% |
| D&A-only floor $25.237B | $160.438B | 25.39% |

None of these scenarios replaces reported FCF in the operating model.

## 3. Reverse DCF

At $351.66 and 12.274B diluted shares, the market-equity target is $4.316T. Using reported TTM FCF $53.273B, the
base discount rate 11.74%, terminal growth 2.5% and a five-year constant growth period requires:

- Implied five-year FCF CAGR: **59.02%**
- Implied year-5 FCF: **$541.694B**
- Reverse-model terminal-value share: **79.92%**

Across discount rates 9%-15% and terminal growth 1.5%-3.0%, the required FCF CAGR ranges from **43.81% to 74.45%**.
This is far above the observed FY2022-FY2025 FCF CAGR of 6.88%. If revenue merely followed the observed 12.51% CAGR,
year-5 revenue would be $803.885B and the base reverse DCF would require a 67.38% FCF margin, versus the current TTM
operating margin of 33.11%. This comparison exposes the burden of the market price; it is not a forecast.

Revenue growth, target operating margin, working capital, CAPEX, taxes, and terminal growth cannot all be uniquely
recovered from a single market price. The engine reports this system as `underdetermined` rather than selecting an
arbitrary combination that makes the price fit.

## 4. Earnings-quality gate

- Q1 2026 net equity-securities gain: $36.915B; issuer-stated after-tax net-income effect $28.7B and diluted EPS
  effect +$2.35. The separate cash-flow removal for net debt/equity-security gains was $36.804B.
- Q2 2026 net equity-securities gain: $99.031B; issuer-stated after-tax net-income effect $77.1B and diluted EPS
  effect +$6.26. The separate cash-flow removal for net debt/equity-security gains was $98.999B.
- Derived TTM EPS remains null because annual/YTD and quarter EPS use different weighted diluted denominators.
- Normalized TTM EPS remains null because every after-tax adjustment required for a complete TTM normalization is not
  independently established.
- The P/E model remains disabled.

## 5. Confidence separation

| Dimension | State |
|---|---|
| Financial data quality | Official hash-verified source with unaudited interim periods |
| Forecast quality | Insufficient except for CAPEX guidance |
| Valuation model confidence | Research only |
| Operational judgement eligibility | False |

The historical `confidence_score` shown for a provisional row is now labelled research-input completeness, not investment
confidence. A provisional row remains ineligible for operational valuation and new trading signals.

## 6. Reproduction

```powershell
python scripts/audit_googl_dcf.py `
  --financials <normalized-financials.csv> `
  --universe <verified-universe.csv> `
  --risk-free-rate 0.0524 `
  --output <isolated-audit-output.json>
```

The script does not download data, does not call a paid API, and does not change production snapshots.

## Official sources

- Alphabet Q2 2025 earnings call and revised $85B CAPEX outlook:
  https://abc.xyz/2025-q2-earnings-call
- Alphabet FY2025/Q4 release and 2026 $175B-$185B CAPEX outlook:
  https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf
- Alphabet Q2 2026 release and TTM cash-flow table:
  https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf

