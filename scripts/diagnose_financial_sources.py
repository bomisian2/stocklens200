from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.financials import (  # noqa: E402
    SEC_COMPANY_FACTS_BULK_URL,
    SEC_COMPANY_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    probe_financial_source,
)


def main() -> int:
    cik = 1652044
    targets = [
        ("sec_api", SEC_COMPANY_FACTS_URL.format(cik=cik)),
        ("sec_api", SEC_SUBMISSIONS_URL.format(cik=cik)),
        ("sec_bulk", SEC_COMPANY_FACTS_BULK_URL),
    ]
    diagnostics = [
        probe_financial_source(url, provider=provider).to_dict()
        for provider, url in targets
    ]
    print(json.dumps({"diagnostics": diagnostics}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
