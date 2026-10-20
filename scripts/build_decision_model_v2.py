from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.decision_model_v2 import build_proxy_decision, write_decision_and_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="Build offline Decision Model v2 proxy artifact")
    parser.add_argument("--top10-root", type=Path, required=True)
    parser.add_argument("--private-data-root", type=Path, required=True)
    parser.add_argument("--decision-time")
    args = parser.parse_args()
    root = args.top10_root.resolve()
    private = args.private_data_root.resolve()
    decision_time = args.decision_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    decision = build_proxy_decision(
        price_history_path=root / "price-history.json",
        consensus_path=root / "top100-consensus.json",
        valuation_history_path=root / "valuation-history.json",
        industry_map_path=root / "industry-map.json",
        macro_path=root / "macro-snapshot.json",
        config_path=Path(__file__).resolve().parents[1] / "config" / "decision-model-v2.json",
        decision_time=decision_time,
    )
    output, ledger = write_decision_and_ledger(
        decision=decision,
        decision_path=private / "decision" / "nvda-decision-model-v2.json",
        ledger_path=private / "decision" / "decision-model-v2-signal-ledger.json",
    )
    print(f"Decision Model v2 written: {output.name}; ledger: {ledger.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
