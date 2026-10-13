"""Local-only personal research data pipeline.

Raw and normalized outputs belong under ``private_data/`` and are deliberately
excluded from Git.  Nothing in this package is imported by the public app.
"""

from .ir_financials import collect_pilot_financials
from .market_data import AlphaVantagePersonalClient, compute_technical_snapshot

__all__ = [
    "AlphaVantagePersonalClient",
    "collect_pilot_financials",
    "compute_technical_snapshot",
]
