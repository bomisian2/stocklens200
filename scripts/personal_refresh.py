from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.personal import AlphaVantagePersonalClient, collect_pilot_financials
from stocklens.personal.storage import atomic_write_json, load_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh local-only StockLens personal research data")
    parser.add_argument("--data-dir", default=ROOT / "private_data", help="Ignored local data directory")
    parser.add_argument(
        "--market-tickers",
        nargs="*",
        default=[],
        help="Optional Alpha Vantage EOD tickers (requires ALPHAVANTAGE_API_KEY)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    status = collect_pilot_financials(data_dir)
    if args.market_tickers:
        key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
        if not key:
            status["market"] = {
                "status": "blocked",
                "reason": "ALPHAVANTAGE_API_KEY is missing",
                "tickers_verified": 0,
            }
        else:
            client = AlphaVantagePersonalClient(key)
            results = [client.collect_daily(ticker, data_dir) for ticker in args.market_tickers]
            status["market"] = {
                "status": "verified" if all(item.status == "verified" for item in results) else "partial",
                "tickers_verified": sum(item.status == "verified" for item in results),
                "results": [item.__dict__ for item in results],
                "scope": "local_personal_only",
            }
        atomic_write_json(data_dir / "status.json", status)
    persisted = load_json(data_dir / "status.json") or status
    print(json.dumps(persisted, ensure_ascii=False, indent=2))
    return 0 if persisted["financial_tickers_verified"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
