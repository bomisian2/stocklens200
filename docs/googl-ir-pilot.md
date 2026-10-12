# GOOGL official-IR financial pilot

Pilot date: 2026-10-12

Valuation market date: 2026-10-09
Ticker / CIK: GOOGL / 0001652044

## Outcome

| Metric | Result |
|---|---:|
| Official PDF artifacts downloaded and hash checked | 3 |
| Real `FinancialPeriod` rows imported | 5 |
| Verified annual rows | 4 |
| Degraded unaudited quarterly rows | 1 |
| Formal valuations (`evaluated`) | 0 |
| Provisional operational valuations | 0 |
| Holds | 1 |

The import is a real-data success and a valuation hold. A clean operational run does not publish the
research-only DCF range because the current model consumes annual rows only: the latest consumed annual
input was available on 2026-02-05, more than the 190-day freshness limit at the 2026-10-09 market date.
The newer Q2 report proves provider freshness but is not silently treated as a trailing-year statement.

## Source artifacts

| Report | Official URL | SHA-256 |
|---|---|---|
| Alphabet 2024 annual report | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2024-annual-report.pdf | `745700dad59d69a137d705194b7944c91dc1fb1d5de6b3866df0da7db9cee2d6` |
| Alphabet 2025 Q4/full-year release | https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf | `8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9` |
| Alphabet 2026 Q2 release | https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf | `65f35a2e9c287112121f736321c7526d603ba8e0dae27acac56c7d7357602aa8` |

The PDFs are not committed. The local manifest records the hashes and the normalized output hash
`bc614226835c75caa59be93932e2c1ccc5020dd8441d48a8af6eec693ff21f8f`.

SEC filing-index pages were used only to cross-check identity and exact acceptance time; the financial
numbers came from the downloaded issuer IR PDFs. Eastern acceptance times were converted to UTC with
the applicable standard/daylight offset:

- 2024 10-K: accession `0001652044-25-000014`, accepted 2025-02-04 20:41:40 ET =
  2025-02-05T01:41:40Z.
- 2025 10-K: accession `0001652044-26-000018`, accepted 2026-02-04 21:56:03 ET =
  2026-02-05T02:56:03Z.
- 2026 Q2 8-K: accession `0001652044-26-000066`, accepted 2026-07-22 16:01:36 ET =
  2026-07-22T20:01:36Z.

## Normalized financials

USD millions except EPS and diluted shares (millions).

| Period | Revenue | Operating income | Net income | Diluted EPS | Diluted shares | CFO | CAPEX | FCF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2022 | 282,836 | 74,842 | 59,972 | 4.56 | 13,159 | 91,495 | 31,485 | 60,010 |
| FY2023 | 307,394 | 84,293 | 73,795 | 5.80 | 12,722 | 101,746 | 32,251 | 69,495 |
| FY2024 | 350,018 | 112,390 | 100,118 | 8.04 | 12,447 | 125,299 | 52,535 | 72,764 |
| FY2025 | 402,836 | 129,039 | 132,170 | 10.81 | 12,230 | 164,713 | 91,447 | 73,266 |
| Q2 2026 | 119,796 | 40,770 | 112,193 | 9.11 | 12,309 | 39,069 | 44,924 | -5,855 |

FCF is reproduced as CFO minus positive CAPEX outflow. The provider rejects a manifest when this
identity fails. Cash, long-term debt, equity, stock compensation, depreciation, and buybacks are also
stored when present.

## EPS distortion and valuation status

Q2 2026 GAAP EPS is not treated as normalized earnings. The issuer release says other income was
$97.983 billion, primarily unrealized equity-security gains; the cash-flow statement removes a
$98.999 billion gain on debt and equity securities. Net income available to common stockholders was
$112.107 billion after $86 million of preferred dividends, which explains the EPS numerator difference
from $112.193 billion total net income.

No after-tax per-share normalization is manufactured. Consequently:

- formal P/E method: unavailable (zero auditable point-in-time P/E observations and no approved
  normalized TTM EPS evidence);
- EV/EBITDA method: unavailable (no permitted point-in-time comparable distribution);
- DCF: can produce a research-only historical range, but lacks point-in-time forecast evidence and the
  current annual-only inputs are stale;
- final status: `hold`, judgement `판단 보류`, eligible for new valuation/signals `false`.

A direct research calculation before the freshness gate produced a very wide historical-only DCF range
of $58.65 / $104.32 / $210.12 with a 50% automatic safety margin and 10.24% discount rate (5.24% U.S.
Treasury 10-year rate plus 5% equity risk premium). This is recorded only to diagnose model behavior;
it is **not** an operational fair value, buy zone, or valuation success.

## Reproduction

After placing the three official PDFs beside a reviewed manifest:

```powershell
python scripts/import_ir_financials.py `
  --manifest <path-to-googl-ir-manifest.json> `
  --data-dir <isolated-output-directory> `
  --tickers GOOGL `
  --as-of 2026-10-09T20:00:00+00:00

Copy-Item data\universe.csv,data\status.json -Destination <isolated-output-directory>

python scripts/update_valuations.py `
  --data-dir <isolated-output-directory> `
  --tickers GOOGL `
  --allow-partial `
  --financial-source issuer-ir `
  --ir-manifest <path-to-googl-ir-manifest.json>
```

The manifest must declare `fixture=false`, use the allow-listed issuer host, match GOOGL's CIK, and
match every PDF hash. Source files, normalized output, and operational output remain separate.

## Next minimum work

1. Import the official 2026 Q1 release and build a reviewed TTM bridge from FY2025 plus H1 2026 minus
   H1 2025.
2. Verify that the TTM bridge handles preferred dividends and the unrealized investment gain without
   creating normalized EPS by assumption.
3. Add a permitted dated price source and then-public GAAP EPS vintages; legacy aggregate bands do not
   satisfy this requirement.
4. Only after those gates pass, recompute a formal value and expand to AMAT, AVGO, QCOM, and INTC.
