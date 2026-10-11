from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.pipeline import update_universe
from stocklens.storage import SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the StockLens 200 last-good universe snapshot")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()

    result = update_universe(SnapshotStore(args.data_dir))
    print(
        f"universe_count={len(result.frame)} source={result.source} "
        f"state={result.state} used_cache={str(result.used_cache).lower()} "
        f"provider_errors={len(result.errors)}"
    )
    for error in result.errors:
        print(f"warning={error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
