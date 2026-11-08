from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.market_calendar import live_collection_window  # noqa: E402


def main() -> int:
    window = live_collection_window(datetime.now(timezone.utc))
    print(f"collect={'true' if window.collect else 'false'}")
    print(f"reason={window.reason}")
    print(f"active_session_date={window.active_session_date or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
