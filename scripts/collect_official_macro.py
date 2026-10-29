from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.personal.macro_snapshot import (
    OfficialMacroSnapshotError,
    collect_official_macro_snapshot,
    write_failed_attempt,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect a local-only official macro snapshot from FRED and Cboe"
    )
    parser.add_argument("--data-dir", default=ROOT / "private_data")
    parser.add_argument(
        "--canary-only",
        action="store_true",
        help="Call only the DFF canary and never publish an active snapshot",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    try:
        result = collect_official_macro_snapshot(
            data_dir,
            qqq_csv_path=data_dir / "market" / "QQQ.csv",
            qqq_manifest_path=data_dir / "market" / "QQQ.manifest.json",
            canary_only=args.canary_only,
        )
    except OfficialMacroSnapshotError as exc:
        write_failed_attempt(data_dir, exc)
        details = exc.details
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "diagnostic_code": type(exc).__name__,
                    "message": str(exc),
                    "last_good_preserved": True,
                    **details,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
