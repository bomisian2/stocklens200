from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.market_calendar import latest_completed_session, market_phase


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    session = latest_completed_session(now)
    print(
        json.dumps(
            {
                "evaluated_at_utc": now.isoformat(),
                "xnys_phase": market_phase(now),
                "latest_completed_session": session.session_date,
                "latest_completed_close_utc": session.close_utc,
                "policy": "diagnostic only; a current intraday quote is not a completed-session snapshot",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
