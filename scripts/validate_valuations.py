from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.valuation import MODEL_VERSION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate stored StockLens valuation output")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--require-tickers", nargs="*", default=[])
    args = parser.parse_args()

    path = args.data_dir / "valuations.csv"
    status_path = args.data_dir / "valuation_status.json"
    financials_path = args.data_dir / "financials.csv"
    if not path.exists() or not status_path.exists() or not financials_path.exists():
        raise RuntimeError("valuation, financial, or status snapshot missing")
    frame = pd.read_csv(path)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    financials = pd.read_csv(financials_path)
    if not args.allow_partial and len(frame) != 200:
        raise RuntimeError(f"expected 200 valuation rows, got {len(frame)}")
    if frame["ticker"].duplicated().any():
        raise RuntimeError("duplicate valuation tickers")
    required = set(args.require_tickers)
    if not required.issubset(set(frame["ticker"])):
        raise RuntimeError(f"required tickers missing: {sorted(required - set(frame['ticker']))}")
    evaluated = frame[frame["status"] == "evaluated"]
    if required and not required.issubset(set(evaluated["ticker"])):
        raise RuntimeError(f"sample tickers were not evaluated: {sorted(required - set(evaluated['ticker']))}")
    if evaluated.empty:
        raise RuntimeError("no companies passed valuation input gates")
    if evaluated["base_value"].isna().any() or (evaluated["base_value"] <= 0).any():
        raise RuntimeError("evaluated rows have missing or non-positive values")
    if evaluated["confidence_score"].lt(0).any() or evaluated["confidence_score"].gt(100).any():
        raise RuntimeError("confidence score outside 0-100")
    if status.get("model_version") != MODEL_VERSION:
        raise RuntimeError("valuation status model version mismatch")
    if status.get("evaluated_count") != len(evaluated):
        raise RuntimeError("valuation status count mismatch")
    if financials.empty or not set(evaluated["ticker"]).issubset(set(financials["ticker"])):
        raise RuntimeError("evaluated tickers lack stored financial periods")
    print(
        f"valuation_count={len(frame)} evaluated={len(evaluated)} "
        f"hold={len(frame) - len(evaluated)} model={MODEL_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

