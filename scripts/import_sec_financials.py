from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.financials import KNOWN_CIKS, merge_cik_maps  # noqa: E402
from stocklens.sec_files import SecFileProvider  # noqa: E402
from stocklens.valuation_storage import ValuationStore  # noqa: E402


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _ticker_cik(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        try:
            ticker, cik = value.split("=", 1)
            result[ticker.strip().upper().replace("-", ".")] = int(cik)
        except (ValueError, TypeError) as exc:
            raise ValueError("--ticker-cik must use TICKER=CIK") from exc
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import user-downloaded official SEC files without making network requests"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--ticker-cik", action="append", default=[])
    parser.add_argument("--as-of", type=_utc, default=datetime.now(timezone.utc))
    args = parser.parse_args()

    provider = SecFileProvider.from_manifest(args.manifest)
    cik_map = merge_cik_maps(
        KNOWN_CIKS,
        provider.cik_map(),
        _ticker_cik(args.ticker_cik),
        provider="sec_offline_import",
    )
    periods = []
    missing: list[str] = []
    for raw_ticker in args.tickers:
        ticker = raw_ticker.upper().replace("-", ".")
        cik = cik_map.get(ticker)
        if cik is None:
            missing.append(f"{ticker}: CIK unavailable")
            continue
        rows = provider.fetch_periods(ticker, cik, as_of=args.as_of)
        if not rows:
            missing.append(f"{ticker}: no eligible point-in-time facts")
            continue
        periods.extend(rows)
    if not periods:
        raise RuntimeError("no verified financial periods were imported")

    store = ValuationStore(args.data_dir)
    imported_tickers = {period.ticker for period in periods}
    retained = [period for period in store.load_financials() if period.ticker not in imported_tickers]
    combined = retained + periods
    store.save_financials(combined)
    provenance = provider.provenance(periods=periods)
    provenance["requested_tickers"] = sorted({ticker.upper() for ticker in args.tickers})
    provenance["missing"] = missing
    provenance["as_of_utc"] = args.as_of.replace(microsecond=0).isoformat()
    store.save_provenance(provenance)
    print(json.dumps({
        "imported_ticker_count": len(imported_tickers),
        "period_count": len(periods),
        "retained_period_count": len(retained),
        "normalized_sha256": provenance["normalized_sha256"],
        "missing_count": len(missing),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
