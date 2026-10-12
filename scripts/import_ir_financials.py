from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.ir_files import IssuerIrFileProvider  # noqa: E402
from stocklens.valuation_storage import ValuationStore  # noqa: E402


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import hash-verified official issuer IR financial-report PDFs"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--as-of", type=_utc, default=datetime.now(timezone.utc))
    args = parser.parse_args()

    provider = IssuerIrFileProvider.from_manifest(args.manifest)
    cik_map = provider.cik_map()
    periods = []
    missing: list[str] = []
    for raw_ticker in args.tickers:
        ticker = raw_ticker.upper().replace("-", ".")
        cik = cik_map.get(ticker)
        if cik is None:
            missing.append(f"{ticker}: official IR manifest has no verified CIK")
            continue
        rows = provider.fetch_periods(ticker, cik, as_of=args.as_of)
        if not rows:
            missing.append(f"{ticker}: no point-in-time eligible period")
            continue
        periods.extend(rows)
    if not periods:
        raise RuntimeError("no issuer IR financial periods were imported")

    store = ValuationStore(args.data_dir)
    imported_tickers = {period.ticker for period in periods}
    retained = [period for period in store.load_financials() if period.ticker not in imported_tickers]
    store.save_financials(retained + periods)
    provenance = provider.provenance(periods=periods)
    provenance.update({
        "requested_tickers": sorted({ticker.upper() for ticker in args.tickers}),
        "missing": missing,
        "as_of_utc": args.as_of.replace(microsecond=0).isoformat(),
    })
    store.save_provenance(provenance)
    print(json.dumps({
        "imported_ticker_count": len(imported_tickers),
        "period_count": len(periods),
        "verified_period_count": sum(row.validation_state == "verified" for row in periods),
        "degraded_period_count": sum(row.validation_state == "degraded" for row in periods),
        "normalized_sha256": provenance["normalized_sha256"],
        "missing_count": len(missing),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
