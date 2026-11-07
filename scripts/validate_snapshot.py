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


def validate_snapshot_integrity(report: dict) -> None:
    """Validate the stored artifact without pretending it is current today."""

    metrics = report["quality"]
    if report["company_count"] != 200:
        raise SystemExit("snapshot must contain exactly 200 companies")
    if report["data_state"] != "healthy":
        raise SystemExit("snapshot was not healthy when it was stored")
    if report["freshness_state"] != "verified":
        raise SystemExit("snapshot market session was not verified when stored")
    if not report["market_session_date"]:
        raise SystemExit("snapshot market session date is missing")
    if not (report.get("cross_validation") or {}).get("passed"):
        raise SystemExit("independent-source cross-validation did not pass")
    if metrics["sector_coverage"] < 0.80 or metrics["country_coverage"] < 0.80:
        raise SystemExit("metadata coverage is below 80%")


def validate_current_freshness(report: dict) -> None:
    """Require the snapshot to match the actual latest completed session."""

    if report["market_session_date"] != report["expected_session_date"]:
        raise SystemExit("snapshot does not represent the latest completed market session")


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

    validate_snapshot_integrity(report)
    if args.strict:
        validate_current_freshness(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
