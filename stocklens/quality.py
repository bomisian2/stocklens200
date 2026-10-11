from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .providers import UniverseRecord


@dataclass(frozen=True)
class CrossValidationResult:
    passed: bool
    primary_count: int
    reference_count: int
    overlapping_tickers: int
    overlap_ratio: float
    comparable_market_caps: int
    market_caps_within_35_percent: int
    market_cap_agreement_ratio: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def cross_validate_universes(
    primary: Iterable[UniverseRecord],
    reference: Iterable[UniverseRecord],
    *,
    minimum_overlap_ratio: float = 0.80,
    minimum_cap_agreement_ratio: float = 0.75,
) -> CrossValidationResult:
    """Compare independently ranked Top-200 sets without hiding disagreements."""
    primary_rows = list(primary)
    reference_rows = list(reference)
    primary_by_ticker = {row.ticker: row for row in primary_rows}
    reference_by_ticker = {row.ticker: row for row in reference_rows}
    overlap = sorted(primary_by_ticker.keys() & reference_by_ticker.keys())

    agreements = 0
    comparable = 0
    for ticker in overlap:
        first = primary_by_ticker[ticker].market_cap
        second = reference_by_ticker[ticker].market_cap
        if first <= 0 or second <= 0:
            continue
        comparable += 1
        relative_difference = abs(first - second) / max(first, second)
        if relative_difference <= 0.35:
            agreements += 1

    denominator = max(1, min(len(primary_rows), len(reference_rows)))
    overlap_ratio = len(overlap) / denominator
    agreement_ratio = agreements / comparable if comparable else 0.0
    passed = overlap_ratio >= minimum_overlap_ratio and agreement_ratio >= minimum_cap_agreement_ratio
    if passed:
        reason = "independent Top-200 membership and market-cap checks passed"
    elif overlap_ratio < minimum_overlap_ratio:
        reason = "independent Top-200 membership overlap is below threshold"
    else:
        reason = "independent market-cap agreement is below threshold"

    return CrossValidationResult(
        passed=passed,
        primary_count=len(primary_rows),
        reference_count=len(reference_rows),
        overlapping_tickers=len(overlap),
        overlap_ratio=round(overlap_ratio, 4),
        comparable_market_caps=comparable,
        market_caps_within_35_percent=agreements,
        market_cap_agreement_ratio=round(agreement_ratio, 4),
        reason=reason,
    )
