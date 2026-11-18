from __future__ import annotations

import json
from urllib.parse import urlparse

import requests


CANDIDATES = {
    "alpha_vantage_demo": (
        "https://www.alphavantage.co/query?function=OVERVIEW&symbol=IBM&apikey=demo"
    ),
    "financial_modeling_prep_no_key": (
        "https://financialmodelingprep.com/stable/income-statement?symbol=AAPL"
    ),
    "twelve_data_demo": (
        "https://api.twelvedata.com/income_statement?symbol=AAPL&apikey=demo"
    ),
    "polygon_no_key": "https://api.polygon.io/vX/reference/financials?ticker=AAPL&limit=1",
}


def main() -> int:
    results = []
    for name, url in CANDIDATES.items():
        domain = urlparse(url).hostname
        try:
            response = requests.get(url, timeout=12, stream=True)
            results.append({
                "candidate": name,
                "domain": domain,
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type", "").split(";", 1)[0],
            })
            response.close()
        except requests.RequestException as exc:
            results.append({
                "candidate": name,
                "domain": domain,
                "http_status": None,
                "error_type": type(exc).__name__,
            })
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
