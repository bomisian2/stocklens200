# AMAT, AVGO, QCOM official-result audit (2026-10-13)

The audit is local-only and reproducible with:

```powershell
python scripts/audit_personal_data.py --data-dir private_data
```

It compares normalized output with an independently transcribed issuer-fact
contract and verifies the saved raw bytes against each normalized document
manifest. Raw documents and generated audit JSON stay under ignored
`private_data/`; this file records only the non-sensitive result.

| Ticker | Fiscal periods | Checks | Result | TTM formula | Price / technicals |
|---|---:|---:|---|---|---|
| AMAT | FY2025, Q3 FY2026, 9M FY2026, TTM | 55/55 | verified | FY2025 + 9M FY2026 - 9M FY2025 | 100 raw, unadjusted sessions; 2026-10-12 verified EOD close |
| AVGO | FY2025, Q3 FY2026, 9M FY2026, TTM | 51/51 | verified | FY2025 + 9M FY2026 - 9M FY2025 | blocked; prior provider response was not safely classified |
| QCOM | FY2025, Q3 FY2026, 9M FY2026, TTM | 54/54 | verified | FY2025 + 9M FY2026 - 9M FY2025 | blocked; prior provider response was not safely classified |

## Findings

- All 160 comparisons passed; no before/after correction was required.
- Issuer publication timestamps occur after their stated fiscal period ends, so
  the data is not treated as available before publication.
- Amounts remain USD millions except per-share data. GAAP reported values remain
  separate from issuer non-GAAP guidance.
- QCOM Q3 standalone CFO/CAPEX/FCF remains null because the release presents
  those cash-flow lines on a nine-month basis. Its investment/other income and
  tax benefit are disclosed separately; no normalized EPS is invented.
- TTM diluted EPS and a point-in-time common-share count remain unavailable.
  AMAT's Q3 FY2026 800 million share figure is the three-month diluted
  weighted-average denominator from the issuer statement of operations, not
  shares outstanding on 2026-07-26. Price × 800 million and the resulting FCF
  yield are therefore research proxies, not formal market capitalization or a
  valuation conclusion. Formal PER, fair value, target price, and buy range are
  not produced.

The audit validates transcription and provenance, not the economic accuracy of
future forecasts or an investment recommendation.
