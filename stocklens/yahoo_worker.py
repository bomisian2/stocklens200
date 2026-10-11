"""Bounded subprocess entry point for the optional Yahoo screener fallback."""

from __future__ import annotations

import json
import sys

import yfinance as yf
from yfinance import EquityQuery

from .providers import US_EXCHANGES


def main() -> None:
    query = EquityQuery("is-in", ["exchange", *US_EXCHANGES])
    payload = yf.screen(query, size=250, sortField="intradaymarketcap", sortAsc=False)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write(serialized.encode("utf-8"))


if __name__ == "__main__":
    main()
