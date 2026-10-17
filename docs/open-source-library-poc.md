# External-library isolated PoC

Review date: 2026-10-12

Neither library is added to production requirements. The experiment uses checked-out source trees under an ignored
temporary directory and never invokes FMP, Yahoo or another provider.

## FinanceToolkit

- Official repository: https://github.com/JerBouma/FinanceToolkit
- Reviewed commit: `37ed6f01465b4bfb6718b07e1d65826734f53337`
- Version at that commit: 2.2.2
- License: MIT
- Python: `>=3.11,<3.16`
- Important dependency conflict: FinanceToolkit requires pandas 3+, while StockLens pins pandas `<3`.
- Other dependencies include requests, yfinance, openpyxl, xlrd, PyYAML, python-calamine, NumPy and SciPy.

The PoC dynamically loads the standalone profitability formula module. The same verified GOOGL inputs produced
bit-identical local and library results:

| Metric | Result | Interpretation |
|---|---:|---|
| TTM FCF margin | 11.9482% | Reusable but trivial formula |
| TTM ROE | 48.6586% | Distorted by investment gains |
| Net-income-basis ROIC | 41.5611% | Not StockLens's desired NOPAT ROIC; distorted |

Ten thousand three-ratio iterations took about 3.2 ms in the local run after import. A cold isolated load of the
22 KB formula module, including pandas startup, took about 3.65 seconds and peaked near 38.8 MiB under `tracemalloc`.
The full reviewed checkout occupied 80.53 MiB. Formula execution is cheap, but the package dependency surface and
pandas conflict are not. WACC comparison is blocked because a same-basis
verified beta, benchmark return series and interest expense are unavailable. A DCF comparison is also deferred until
both engines can receive exactly the same forecast, discount and terminal assumptions.

Conclusion: keep the StockLens data provenance and valuation engine. Individual transparent formulas can be used as
test oracles or copied only with attribution/license compliance after review; adopting the full library does not solve
SEC access, data licensing, point-in-time evidence or normalization.

## QuantStats

- Official repository: https://github.com/ranaroussi/quantstats
- Reviewed commit: `9ef4c6d0a4ffe7831f6ebba6a5824fd33a90c05a`
- Version at that commit: 0.0.86
- License: Apache-2.0
- Python: `>=3.10`
- Dependencies include pandas, NumPy, SciPy, matplotlib, seaborn, tabulate, yfinance and python-dateutil.

The reviewed checkout occupied 2.57 MiB, but a full import failed in the current StockLens environment because
matplotlib is intentionally not installed. This confirms that adding even statistics-only use through the package's
public initializer expands the deployment dependencies; no package was installed for the PoC.

The short-term-signal branch commits strategy/trade summaries but not a continuous daily portfolio return series.
Therefore Sharpe, Sortino, CAGR and portfolio MDD are not computed. Converting average trade returns into a daily
portfolio curve would manufacture exposure, overlap, cash and transaction-cost assumptions.

`experiments/library_poc.py` accepts QuantStats input only when it has unique sorted dates, strategy and QQQ daily
returns, no missing values and at least 252 observations. When that dataset exists, the comparison scope is Sharpe,
Sortino, CAGR, MDD and QQQ CAGR. This does not resolve current-constituent bias, adjusted-price point-in-time bias or
market-data redistribution rights.

## Reproduction

```powershell
python experiments/library_poc.py `
  --financials <normalized-financials.csv> `
  --financetoolkit-root <checked-out-FinanceToolkit-root>

# Only after a real daily portfolio/QQQ return series exists:
python experiments/library_poc.py `
  --daily-returns <daily-portfolio-returns.csv> `
  --quantstats-root <checked-out-QuantStats-root>
```

## Recommendation

Do not add either package to `requirements.txt` now. FinanceToolkit provides useful independent formula definitions,
but the tested formulas are simple, its ROIC definition is unsuitable for this GOOGL case, and its current pandas
requirement conflicts with production. QuantStats becomes useful after the signal engine emits an auditable daily
portfolio ledger; until then the necessary input does not exist.
