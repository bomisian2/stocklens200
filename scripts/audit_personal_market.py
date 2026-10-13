from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.personal.market_audit import audit_saved_market_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit ignored local StockLens OHLCV without network calls")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "private_data")
    args = parser.parse_args()
    result = audit_saved_market_data(args.data_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(
        row["data_status"] == "verified"
        for row in result["results"]
        if row["ticker"] in {"AMAT", "QQQ"}
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
