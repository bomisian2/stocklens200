from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yfinance as yf


FIELDS = ("Open", "High", "Low", "Close", "Volume")


def _one_ticker(raw: pd.DataFrame, ticker: str, multiple: bool) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        selected = raw[ticker].copy()
    elif multiple:
        return pd.DataFrame()
    else:
        selected = raw.copy()
    missing = set(FIELDS) - set(selected.columns)
    if missing:
        return pd.DataFrame()
    selected = selected.loc[:, list(FIELDS)].reset_index()
    date_column = selected.columns[0]
    selected = selected.rename(columns={
        date_column: "date", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    selected.insert(1, "ticker", ticker)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tickers = [str(value).upper() for value in args.tickers]
    raw = yf.download(
        tickers=tickers,
        start=args.start,
        end=args.end,
        interval="1d",
        auto_adjust=True,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=20,
    )
    frames = [
        _one_ticker(raw, ticker, multiple=len(tickers) > 1)
        for ticker in tickers
    ]
    usable = [frame for frame in frames if not frame.empty]
    output = pd.concat(usable, ignore_index=True) if usable else pd.DataFrame(
        columns=["date", "ticker", "open", "high", "low", "close", "volume"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False, encoding="utf-8")
    print(json.dumps({
        "requested": len(tickers),
        "returned": int(output["ticker"].nunique()) if not output.empty else 0,
        "rows": len(output),
    }, sort_keys=True))
    return 0 if usable else 2


if __name__ == "__main__":
    raise SystemExit(main())

