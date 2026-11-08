from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.sec_files import build_manifest_artifact  # noqa: E402


def _parse_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a hash-verified manifest for manually downloaded official SEC files"
    )
    parser.add_argument("--companyfacts", type=Path, action="append", required=True)
    parser.add_argument("--submissions", type=Path, action="append", default=[])
    parser.add_argument("--ticker-map", type=Path, default=None)
    parser.add_argument("--obtained-at", type=_parse_timestamp, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    inputs = [*args.companyfacts, *args.submissions]
    if args.ticker_map:
        inputs.append(args.ticker_map)
    for path in inputs:
        if path.resolve().parent != output.parent:
            raise ValueError("manifest and imported files must be in the same directory")

    artifacts = [build_manifest_artifact("companyfacts", path) for path in args.companyfacts]
    artifacts.extend(build_manifest_artifact("submissions", path) for path in args.submissions)
    if args.ticker_map:
        artifacts.append(build_manifest_artifact("ticker_map", args.ticker_map))
    payload = {
        "schema_version": 1,
        "provider": "sec_edgar_official_files",
        "obtained_at_utc": args.obtained_at,
        "artifacts": artifacts,
        "fixture": False,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(output), "artifact_count": len(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
