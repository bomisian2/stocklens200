"""Local-only personal research data pipeline.

Raw and normalized outputs belong under ``private_data/`` and are deliberately
excluded from Git.  Nothing in this package is imported by the public app.
"""

from .ir_financials import collect_pilot_financials
from .analysis import compute_fundamental_indicators, compute_personal_analysis
from .financial_audit import audit_normalized_snapshot, audit_saved_personal_data
from .market_data import AlphaVantagePersonalClient, compute_relative_strength, compute_technical_snapshot
from .market_audit import audit_saved_market_data
from .extended_financials import import_saved_extended_financials
from .estimates import AlphaVantageEstimatesClient, EstimateCollectionResult, normalize_alpha_vantage_estimates
from .planning import build_alpha_vantage_call_plan
from .universe import PERSONAL_PILOT_UNIVERSE, pilot_tickers

__all__ = [
    "AlphaVantagePersonalClient",
    "AlphaVantageEstimatesClient",
    "EstimateCollectionResult",
    "collect_pilot_financials",
    "compute_fundamental_indicators",
    "compute_personal_analysis",
    "compute_relative_strength",
    "compute_technical_snapshot",
    "audit_normalized_snapshot",
    "audit_saved_personal_data",
    "audit_saved_market_data",
    "import_saved_extended_financials",
    "normalize_alpha_vantage_estimates",
    "build_alpha_vantage_call_plan",
    "PERSONAL_PILOT_UNIVERSE",
    "pilot_tickers",
]
