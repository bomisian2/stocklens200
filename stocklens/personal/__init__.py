"""Local-only personal research data pipeline.

Raw and normalized outputs belong under ``private_data/`` and are deliberately
excluded from Git.  Nothing in this package is imported by the public app.
"""

from .ir_financials import collect_pilot_financials
from .analysis import compute_fundamental_indicators, compute_personal_analysis
from .financial_audit import audit_normalized_snapshot, audit_saved_personal_data
from .market_data import AlphaVantagePersonalClient, compute_relative_strength, compute_technical_snapshot

__all__ = [
    "AlphaVantagePersonalClient",
    "collect_pilot_financials",
    "compute_fundamental_indicators",
    "compute_personal_analysis",
    "compute_relative_strength",
    "compute_technical_snapshot",
    "audit_normalized_snapshot",
    "audit_saved_personal_data",
]
