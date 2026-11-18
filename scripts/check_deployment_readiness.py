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
    parser = argparse.ArgumentParser(description="Evaluate the StockLens public-deployment gate")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--rights-file", type=Path, default=ROOT / "docs" / "market-data-rights.json")
    args = parser.parse_args()

    store = SnapshotStore(args.data_dir)
    frame = store.load()
    status = store.load_status()
    stored_errors = stored_snapshot_errors(frame, status)
    current, current_errors = operational_freshness(frame, status)
    rights = json.loads(args.rights_file.read_text(encoding="utf-8"))
    rights_errors = [] if rights.get("public_deployment_allowed") is True else [
        "public display and redistribution rights are not confirmed"
    ]
    blockers = [*stored_errors, *current_errors, *rights_errors]
    report = {
        "ready": not blockers,
        "stored_snapshot": {"passed": not stored_errors, "errors": stored_errors},
        "live_freshness": {
            "passed": not current_errors,
            "state": current.state,
            "expected_session_date": current.expected_session_date,
            "errors": current_errors,
        },
        "data_rights": {
            "passed": not rights_errors,
            "reviewed_at": rights.get("reviewed_at"),
            "errors": rights_errors,
        },
        "blockers": blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
