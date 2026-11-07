from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.legacy_assets import audit_legacy_assets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of a legacy StockLens source/export; never creates valuation evidence"
    )
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = audit_legacy_assets(args.source_root)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
