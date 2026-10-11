from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.market_calendar import latest_completed_session
from stocklens.storage import SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a stored StockLens universe snapshot")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    store = SnapshotStore(args.data_dir)
    frame = store.load()
    status = store.load_status()
    metrics = store.quality_metrics(frame)
    report = {
        "company_count": len(frame),
        "data_state": status.get("data_state"),
        "freshness_state": status.get("freshness_state"),
        "market_session_date": status.get("market_session_date"),
        "expected_session_date": latest_completed_session(datetime.now(timezone.utc)).session_date,
        "quality": metrics,
        "cross_validation": status.get("cross_validation"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.strict:
        if status.get("freshness_state") != "verified":
            raise SystemExit("snapshot freshness is not verified")
        if status.get("market_session_date") != report["expected_session_date"]:
            raise SystemExit("snapshot does not represent the latest completed market session")
        if not (status.get("cross_validation") or {}).get("passed"):
            raise SystemExit("independent-source cross-validation did not pass")
        if metrics["sector_coverage"] < 0.80 or metrics["country_coverage"] < 0.80:
            raise SystemExit("metadata coverage is below 80%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
