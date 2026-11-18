from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.snapshot_validation import operational_freshness, stored_snapshot_errors
from stocklens.storage import SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a stored StockLens universe snapshot")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--require-current",
        action="store_true",
        help="also require the latest completed XNYS session (live/operations only)",
    )
    args = parser.parse_args()

    store = SnapshotStore(args.data_dir)
    frame = store.load()
    status = store.load_status()
    metrics = store.quality_metrics(frame)
    stored_errors = stored_snapshot_errors(frame, status)
    current, current_errors = operational_freshness(frame, status)
    report = {
        "company_count": len(frame),
        "data_state": status.get("data_state"),
        "freshness_state": status.get("freshness_state"),
        "market_session_date": status.get("market_session_date"),
        "expected_session_date": current.expected_session_date,
        "quality": metrics,
        "cross_validation": status.get("cross_validation"),
        "checks": {
            "stored_integrity": {"passed": not stored_errors, "errors": stored_errors},
            "operational_freshness": {
                "passed": not current_errors,
                "state": current.state,
                "errors": current_errors,
            },
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.strict:
        if stored_errors:
            raise SystemExit("; ".join(stored_errors))
    if args.require_current and current_errors:
        raise SystemExit("; ".join(current_errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
