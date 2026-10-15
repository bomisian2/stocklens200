from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stocklens.personal.decision_contracts import (
    CONTRACT_ROLES,
    validate_ready_contracts,
)


DECISION_TIME = "2026-10-14T23:00:00Z"
TICKERS = ("AVGO", "NVDA")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _manifest(
    role: str,
    classification: str,
    raw_path: Path,
    *,
    records: object,
    **extra: object,
) -> dict:
    return {
        "schema_version": f"{role.replace('_', '-')}-v1",
        "contract_version": "same-date-ready-contract-v1",
        "evidence_classification": classification,
        "decision_time": DECISION_TIME,
        "target_tickers": list(TICKERS),
        "source_files": [
            {
                "path": raw_path.name,
                "source_hash": _hash(raw_path),
                "available_at": "2026-10-13T20:00:00Z",
            }
        ],
        "records": records,
        **extra,
    }


def _build_contracts(
    tmp_path: Path, *, classification: str = "actual_local_evidence"
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    raw_hashes: dict[str, str] = {}
    for role in CONTRACT_ROLES:
        raw_path = tmp_path / f"{role}-raw.json"
        _write_json(raw_path, {"role": role, "fixture": True})
        raw_hashes[role] = _hash(raw_path)

    eps_records = [
        {
            "ticker": ticker,
            "accounting_basis": "gaap",
            "forecast_horizon": "FY1",
            "currency": "USD",
            "unit": "currency_per_diluted_share",
            "as_of": "2026-10-13",
            "available_at": "2026-10-13T20:00:00Z",
            "provider": "synthetic test provider",
            "usage_scope": "personal_local_research",
            "eps_low": 9.0,
            "eps_base": 10.0,
            "eps_high": 11.0,
            "source_hash": raw_hashes["pit_forward_eps"],
        }
        for ticker in TICKERS
    ]
    eps_path = tmp_path / "pit-forward-eps.json"
    _write_json(
        eps_path,
        _manifest(
            "pit_forward_eps",
            classification,
            tmp_path / "pit_forward_eps-raw.json",
            records=eps_records,
        ),
    )
    paths["pit_forward_eps"] = eps_path

    per_records: list[dict] = []
    start = date(2024, 1, 2)
    for ticker in TICKERS:
        for index in range(20):
            observation = start + timedelta(days=index * 45)
            forward_eps = 8.0 + index / 10
            forward_per = 20.0 + index / 10
            per_records.append(
                {
                    "ticker": ticker,
                    "as_of": observation.isoformat(),
                    "available_at": datetime.combine(
                        observation - timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ).replace(hour=22).isoformat(),
                    "price": forward_eps * forward_per,
                    "forward_eps": forward_eps,
                    "forward_per": forward_per,
                    "underlying_eps_hash": raw_hashes["pit_forward_eps"],
                    "accounting_basis": "gaap",
                    "forecast_horizon": "FY1",
                    "currency": "USD",
                    "unit": "multiple",
                    "split_adjustment_policy": "price_and_eps_split_adjusted",
                    "source_hash": raw_hashes["company_pit_forward_per"],
                }
            )
    per_path = tmp_path / "company-per.json"
    _write_json(
        per_path,
        _manifest(
            "company_pit_forward_per",
            classification,
            tmp_path / "company_pit_forward_per-raw.json",
            records=per_records,
        ),
    )
    paths["company_pit_forward_per"] = per_path

    latest_session = "2026-07-19"
    relative_records = []
    for role, symbol, multiple in (
        ("qqq", "QQQ", 25.0),
        ("soxx", "SOXX", 27.0),
        ("peer_basket", "PEERS", 24.0),
    ):
        relative_records.append(
            {
                "role": role,
                "symbol": symbol,
                "as_of": latest_session,
                "available_at": "2026-07-19T22:00:00Z",
                "forward_multiple": multiple,
                "accounting_basis": "gaap",
                "forecast_horizon": "FY1",
                "currency": "USD",
                "unit": "multiple",
                "weighting_method": "equal_weight",
                "constituents": [
                    {
                        "ticker": symbol,
                        "weight": 1.0,
                        "accounting_basis": "gaap",
                        "forecast_horizon": "FY1",
                        "currency": "USD",
                    }
                ],
                "source_hash": raw_hashes["relative_forward_multiples"],
            }
        )
    relative_path = tmp_path / "relative-multiples.json"
    _write_json(
        relative_path,
        _manifest(
            "relative_forward_multiples",
            classification,
            tmp_path / "relative_forward_multiples-raw.json",
            records=relative_records,
        ),
    )
    paths["relative_forward_multiples"] = relative_path

    filing_records = [
        {
            "ticker": ticker,
            "filing_date": "2026-08-27",
            "timing_basis": "conservative_next_session",
            "accepted_at": None,
            "usable_from_session": "2026-08-28",
            "source_hash": raw_hashes["filing_timing"],
        }
        for ticker in TICKERS
    ]
    filing_path = tmp_path / "filing-timing.json"
    _write_json(
        filing_path,
        _manifest(
            "filing_timing",
            classification,
            tmp_path / "filing_timing-raw.json",
            records=filing_records,
        ),
    )
    paths["filing_timing"] = filing_path

    price_start = date(2026, 1, 1)
    price_records = {
        symbol: [
            {
                "date": (price_start + timedelta(days=index)).isoformat(),
                "adjusted_close": 100.0 + index,
                "source_hash": raw_hashes["adjusted_price_history"],
            }
            for index in range(200)
        ]
        for symbol in (*TICKERS, "QQQ", "SOXX")
    }
    price_path = tmp_path / "adjusted-prices.json"
    _write_json(
        price_path,
        _manifest(
            "adjusted_price_history",
            classification,
            tmp_path / "adjusted_price_history-raw.json",
            records=price_records,
            adjustment_policy="split_and_cash_dividend_adjusted",
            corporate_action_policy="provider back-adjusted with action log",
            calendar="XNYS",
        ),
    )
    paths["adjusted_price_history"] = price_path

    rights_records = [
        {
            "source_hash": source_hash,
            "usage_scope": "personal_local_research",
            "storage_allowed": True,
            "derivative_analysis_allowed": True,
            "local_display_allowed": True,
            "public_display_allowed": False,
            "reviewed_at": "2026-10-01T00:00:00Z",
            "expires_at": "2027-10-01T00:00:00Z",
        }
        for role, source_hash in raw_hashes.items()
        if role != "rights"
    ]
    rights_path = tmp_path / "rights.json"
    _write_json(
        rights_path,
        _manifest(
            "rights",
            classification,
            tmp_path / "rights-raw.json",
            records=rights_records,
        ),
    )
    paths["rights"] = rights_path
    return paths


def _validate(paths: dict[str, Path]) -> dict:
    return validate_ready_contracts(paths, decision_time=DECISION_TIME, tickers=TICKERS)


def _change(path: Path, mutator) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutator(value)
    _write_json(path, value)


def test_all_requirements_pass_only_after_actual_validator_results(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path))
    assert report["schema_valid"] is True
    assert report["promotion_ready"] is True
    assert all(row["passed"] is True for row in report["requirements"])
    assert report["decision_created"] is False


def test_synthetic_fixture_is_distinguished_and_never_promotion_ready(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, classification="synthetic_fixture"))
    assert report["schema_valid"] is True
    assert report["promotion_ready"] is False
    assert report["evidence_classification"] == "synthetic_fixture"
    assert all(row["passed"] is False for row in report["requirements"])
    assert all(row["status"] == "fixture_validated" for row in report["requirements"])


@pytest.mark.parametrize("invalid_basis", ["unknown", "", "gaap_adjusted_guess"])
def test_unknown_eps_basis_is_blocked(tmp_path: Path, invalid_basis: str) -> None:
    paths = _build_contracts(tmp_path)
    _change(
        paths["pit_forward_eps"],
        lambda value: value["records"][0].update(accounting_basis=invalid_basis),
    )
    report = _validate(paths)
    row = report["requirements"][0]
    assert row["passed"] is False
    assert "accounting_basis is not approved" in row["reason"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("currency", "EUR", "must match"),
        ("unit", "unknown_unit", "unit is not approved"),
    ],
)
def test_currency_and_unit_mismatch_are_blocked(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    paths = _build_contracts(tmp_path)
    _change(paths["pit_forward_eps"], lambda payload: payload["records"][0].update({field: value}))
    report = _validate(paths)
    assert report["promotion_ready"] is False
    assert message in report["requirements"][0]["reason"]


def test_future_available_at_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(
        paths["pit_forward_eps"],
        lambda value: value["records"][0].update(available_at="2026-10-15T00:00:00Z"),
    )
    report = _validate(paths)
    assert "later than decision_time" in report["requirements"][0]["reason"]


def test_source_hash_link_break_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / "pit_forward_eps-raw.json"
    raw_path.write_text('{"changed":true}', encoding="utf-8")
    report = _validate(paths)
    assert "source_hash mismatch" in report["requirements"][0]["reason"]


def test_missing_split_policy_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(
        paths["company_pit_forward_per"],
        lambda value: value["records"][0].pop("split_adjustment_policy"),
    )
    report = _validate(paths)
    row = next(row for row in report["requirements"] if row["id"] == "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "split_adjustment_policy" in row["reason"]


def test_same_day_entry_without_acceptance_timestamp_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(
        paths["filing_timing"],
        lambda value: value["records"][0].update(usable_from_session="2026-08-27"),
    )
    report = _validate(paths)
    row = next(row for row in report["requirements"] if row["id"] == "sec_acceptance_or_conservative_next_session")
    assert row["passed"] is False
    assert "same-day entry is blocked" in row["reason"]


@pytest.mark.parametrize("mode", ["missing", "expired", "insufficient"])
def test_rights_manifest_missing_expired_or_insufficient_is_blocked(
    tmp_path: Path, mode: str
) -> None:
    paths = _build_contracts(tmp_path)
    if mode == "missing":
        paths.pop("rights")
    elif mode == "expired":
        _change(paths["rights"], lambda value: value["records"][0].update(expires_at="2026-10-01T00:00:00Z"))
    else:
        _change(paths["rights"], lambda value: value["records"][0].update(local_display_allowed=False))
    report = _validate(paths)
    row = next(row for row in report["requirements"] if row["id"] == "decision_display_rights")
    assert row["passed"] is False
    assert row["status"] == "blocked"
