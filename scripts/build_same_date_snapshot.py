from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.personal.decision_model import DecisionModelError
from stocklens.personal.same_date_builder import build_same_date_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a hash-verified local same-date snapshot")
    parser.add_argument("--decision-ledger", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--regime", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--decision-time", required=True)
    args = parser.parse_args()
    try:
        output = build_same_date_snapshot(
            decision_ledger_path=args.decision_ledger,
            consensus_path=args.consensus,
            regime_path=args.regime,
            output_dir=args.output_dir,
            decision_time=args.decision_time,
        )
    except DecisionModelError as exc:
        parser.exit(2, f"blocked: {exc}\n")
    print(output)


if __name__ == "__main__":
    main()
