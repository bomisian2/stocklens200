# GOOGL official-IR TTM pilot

Pilot date: 2026-10-12

Valuation market date: 2026-10-09

Ticker / CIK: GOOGL / 0001652044

## Result

| Metric | Result |
|---|---:|
| Hash-verified official PDFs | 6 |
| Source-normalized periods | 10 |
| Derived TTM periods | 1 |
| Total `FinancialPeriod` rows | 11 |
| Formal valuations (`evaluated`) | 0 |
| Research-only valuations (`provisional`) | 1 |

The pilot now has current TTM cash-flow inputs, but it is not a formal valuation success. The DCF result is
historical-only, while point-in-time forecasts, an independent financial-statement cross-check and dated P/E
evidence remain unavailable. The output is therefore `provisional`, its investment judgement is `판단 보류`, and
it is ineligible for new valuation or trading signals.

## Source artifacts and timing

| Report | Official URL | SHA-256 |
|---|---|---|
| Alphabet 2024 annual report | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2024-annual-report.pdf | `745700dad59d69a137d705194b7944c91dc1fb1d5de6b3866df0da7db9cee2d6` |
| Alphabet 2025 Q2 release | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q2/2025q2-alphabet-earnings-release.pdf | `3581be042fcbda6ea71185d5aef47458ce3a56cf6b16022d61a997d1807a4de5` |
| Alphabet 2025 Q3 release | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q3/2025q3-alphabet-earnings-release.pdf | `dc759cd42f4b8b2935698e70dd21bb674cb10775bfda5f3990cda75f4c0428fd` |
| Alphabet 2025 Q4/full-year release | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf | `8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9` |
| Alphabet 2026 Q1 release | https://s206.q4cdn.com/479360582/files/doc_financials/2026/q1/2026q1-alphabet-earnings-release.pdf | `a219b3b16dd2471faba3ab040154e29ee1ba61914de82ea82fb027038412d56b` |
| Alphabet 2026 Q2 release | https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf | `65f35a2e9c287112121f736321c7526d603ba8e0dae27acac56c7d7357602aa8` |

The PDFs are not committed. The manifest and output preserve document hashes and the import produced normalized
SHA-256 `ceb50cd06f6f5d8fbbaecf05862fde4f8991fdbd9ef5a7374b788f100c4b3736`.
SEC filing-index pages only cross-check identity and exact acceptance time; financial values come from issuer PDFs.
The new exact UTC availability timestamps are 2025-07-23T20:01:33Z (2025 Q2),
2025-10-29T20:01:48Z (2025 Q3), 2026-04-29T20:01:35Z (2026 Q1), and
2026-07-22T20:01:36Z (2026 Q2).

## TTM reconstruction and cross-check

The bridge is `FY2025 + H1 2026 - H1 2025`. Every required flow also equals the sum of Q3 2025, Q4 2025,
Q1 2026 and Q2 2026. Values below are USD millions.

| TTM to 2026-06-30 | Bridge / four-quarter result |
|---|---:|
| Revenue | 445,866 |
| Operating income | 147,628 |
| Net income | 244,205 |
| Common-stockholder net income | 244,119 |
| Pretax income | 299,269 |
| Income tax | 55,064 |
| Other income | 151,641 |
| Debt/equity securities gain adjustment | 149,012 |
| CFO | 185,675 |
| CAPEX | 132,402 |
| FCF | 53,273 |
| Stock compensation | 28,147 |
| Depreciation and amortization | 25,237 |
| Dividends paid | 10,303 |

The issuer's Q2 release independently reports the same TTM CFO, CAPEX and FCF. The importer rejects a mismatch in
revenue, operating income, net income, pretax income, tax, CFO, CAPEX or FCF.

EPS deliberately remains null in the derived TTM row. Annual/YTD arithmetic gives 19.93, while four quarterly EPS
values total 19.91. Both are diagnostics only because each statement uses a different weighted-average diluted
share denominator. A TTM EPS or normalized EPS is not manufactured from them.

## Investment-gain distortion

The issuer quantified the Q1 2026 equity-security gain at $36.915B and said it raised net income by $28.7B and
diluted EPS by $2.35. Q2 2026 reported a $99.031B equity-security gain and said it raised net income by $77.1B and
diluted EPS by $6.26. The H1 cash-flow statement removes $135.803B of debt/equity security gains, and the TTM bridge
contains $149.012B. Those facts demonstrate severe GAAP EPS distortion, but they do not by themselves provide a
fully audited tax allocation for every TTM component. `normalized_net_income` and `normalized_diluted_eps` therefore
remain null and the P/E model stays disabled.

## Research-only DCF

Using TTM FCF $53.273B, 12.274B diluted shares, the 2026-10-09 official 10-year Treasury rate 5.24%, the existing
model's 5% equity-risk premium and historical-only growth calculation produced:

| Conservative | Base | Optimistic | Automatic safety margin | Status |
|---:|---:|---:|---:|---|
| $38.26 | $64.44 | $118.09 | 50% | `provisional` / 판단 보류 |

This range is a model-behavior diagnostic, not an operational fair value or buy recommendation. It is highly
sensitive to the current AI CAPEX cycle, has no approved forward forecast, and has no independent source cross-check.
The current market price is not used to scale intrinsic value.

The detailed audit in `docs/googl-dcf-audit.md` classifies CFO-minus-CAPEX as a levered equity-cash-flow proxy rather
than FCFF, shows every discounted cash-flow step, withholds an invalid enterprise-to-equity bridge, and adds a
research-only reverse DCF. The $64.44 result is reproduced rather than raised.

## General reusable IR import procedure

1. Download only issuer-hosted annual/quarterly reports whose reuse and display scope has been reviewed.
2. Record issuer identity, CIK, official URL, retrieval time and SHA-256 before parsing.
3. Preserve FY, YTD and single-quarter types; never label a cumulative statement as a quarter.
4. Store exact SEC acceptance time when independently available; otherwise mark conservative timing as estimated.
5. Reconcile statement identities and FCF, then require FY/YTD and four-quarter TTM agreement.
6. Keep GAAP, common-stockholder, preferred-dividend and securities-gain fields separate.
7. Withhold weighted-denominator EPS and normalization unless the issuer evidence supports the exact adjustment.
8. Keep source files, normalized rows and valuation output separate, and never commit unlicensed source documents.

## Reproduction

```powershell
python scripts/import_ir_financials.py `
  --manifest <reviewed-googl-ir-manifest.json> `
  --data-dir <isolated-output-directory> `
  --tickers GOOGL `
  --as-of 2026-10-09T22:00:00+00:00

python scripts/update_valuations.py `
  --data-dir <isolated-output-directory> `
  --tickers GOOGL --max-companies 1 --allow-partial `
  --financial-source issuer-ir `
  --ir-manifest <reviewed-googl-ir-manifest.json>
```

The production `data/` directory is not used for this pilot and no result is deployed.
