from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from stocklens.market_calendar import xnys_session_bounds, xnys_sessions
from stocklens.personal.decision_contracts import (
    CONTRACT_ROLES,
    canonical_record_hash,
    validate_ready_contracts,
)

DECISION_TIME = "2026-10-14T23:00:00Z"
TICKERS = ("AVGO", "NVDA")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _raw(path: Path, role: str, records: Any) -> str:
    _write_json(
        path,
        {
            "raw_schema_version": f"{role.replace('_', '-')}-source-v1",
            "source": "synthetic deterministic contract fixture",
            "records": records,
        },
    )
    return _hash(path)


def _manifest(
    role: str,
    classification: str,
    raw_path: Path,
    records: Any,
    *,
    source_available_at: str = "2026-10-13T18:00:00Z",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema_version": f"{role.replace('_', '-')}-v1",
        "contract_version": "same-date-ready-contract-v1",
        "evidence_classification": classification,
        "decision_time": DECISION_TIME,
        "target_tickers": list(TICKERS),
        "source_files": [{
            "path": raw_path.name,
            "source_hash": _hash(raw_path),
            "available_at": source_available_at,
        }],
        "records": records,
        **extra,
    }


def _bound_rows(raw_rows: list[dict[str, Any]], source_hash: str, *, canonical: bool = False) -> list[dict[str, Any]]:
    rows = []
    for raw_row in raw_rows:
        extra = {"canonical_record_hash": canonical_record_hash(raw_row)} if canonical else {}
        rows.append({**raw_row, "source_hash": source_hash, **extra})
    return rows


def _build_contracts(
    tmp_path: Path,
    *,
    classification: str = "actual_local_evidence",
    eps_basis: str = "gaap",
    eps_as_of: str = "2026-10-13",
    historical_eps_future: bool = False,
    duplicate_relative_role: bool = False,
    aggregate_delta: float = 0.0,
    constituent_mismatch: str | None = None,
    weekend_price: bool = False,
    filing_saturday: bool = False,
    filing_basis: str = "conservative_next_session",
    filing_accepted_at: str | None = None,
    filing_usable_from: str | None = None,
    regime_as_of: str = "2026-10-14",
    regime_available_at: str = "2026-10-14T21:00:00Z",
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    source_hashes: dict[str, str] = {}

    # PIT EPS raw bytes and deterministic normalized rows.
    eps_raw_rows = [
        {
            "ticker": ticker,
            "accounting_basis": eps_basis,
            "forecast_horizon": "FY1",
            "currency": "USD",
            "unit": "currency_per_diluted_share",
            "as_of": eps_as_of,
            "available_at": "2026-10-13T20:00:00Z",
            "provider": "synthetic provider",
            "usage_scope": "personal_local_research",
            "eps_low": 9.0,
            "eps_base": 10.0,
            "eps_high": 11.0,
        }
        for ticker in TICKERS
    ]
    eps_raw = tmp_path / "pit-forward-eps-source.json"
    source_hashes["pit_forward_eps"] = _raw(eps_raw, "pit_forward_eps", eps_raw_rows)
    eps_manifest = tmp_path / "pit-forward-eps.json"
    _write_json(
        eps_manifest,
        _manifest(
            "pit_forward_eps",
            classification,
            eps_raw,
            _bound_rows(eps_raw_rows, source_hashes["pit_forward_eps"], canonical=True),
        ),
    )
    paths["pit_forward_eps"] = eps_manifest

    # Historical PER observations each embed their exact point-in-time EPS row.
    history_sessions = xnys_sessions("2023-01-03", "2026-09-30")[::45][:20]
    per_raw_rows: list[dict[str, Any]] = []
    for ticker in TICKERS:
        for index, session in enumerate(history_sessions):
            session_open, session_close = xnys_session_bounds(session)
            eps_available = session_open - timedelta(hours=1)
            eps_source_available = eps_available - timedelta(hours=1)
            if historical_eps_future and ticker == TICKERS[0] and index == 0:
                eps_source_available = datetime(2026, 1, 1, tzinfo=timezone.utc)
            forward_eps = 8.0 + index / 10
            forward_per = 20.0 + index / 10
            historical_eps = {
                "ticker": ticker,
                "accounting_basis": "gaap",
                "forecast_horizon": "FY1",
                "currency": "USD",
                "unit": "currency_per_diluted_share",
                "as_of": session.isoformat(),
                "available_at": eps_available.isoformat(),
                "eps_base": forward_eps,
                "source": "synthetic PIT archive",
            }
            per_raw_rows.append({
                "ticker": ticker,
                "as_of": session.isoformat(),
                "available_at": session_close.isoformat(),
                "price": forward_eps * forward_per,
                "forward_eps": forward_eps,
                "forward_per": forward_per,
                "eps_source_available_at": eps_source_available.isoformat(),
                "eps_record": historical_eps,
                "accounting_basis": "gaap",
                "forecast_horizon": "FY1",
                "currency": "USD",
                "unit": "multiple",
                "split_adjustment_policy": "price_and_eps_split_adjusted",
            })
    per_raw = tmp_path / "company-per-source.json"
    source_hashes["company_pit_forward_per"] = _raw(per_raw, "company_pit_forward_per", per_raw_rows)
    per_rows = [
        {**row, "source_hash": source_hashes["company_pit_forward_per"],
         "underlying_eps_record_hash": canonical_record_hash(row["eps_record"])}
        for row in per_raw_rows
    ]
    per_manifest = tmp_path / "company-per.json"
    _write_json(per_manifest, _manifest("company_pit_forward_per", classification, per_raw, per_rows))
    paths["company_pit_forward_per"] = per_manifest

    # QQQ, SOXX and peer aggregates are reproducible from constituent price/EPS.
    relative_raw_rows: list[dict[str, Any]] = []
    roles = [("qqq", "QQQ"), ("soxx", "SOXX"), ("peer_basket", "PEER")]
    if duplicate_relative_role:
        roles[-1] = ("soxx", "PEER")
    for role, prefix in roles:
        constituents = []
        for index in range(2):
            ticker = f"{prefix}{index + 1}"
            basis = "non_gaap" if constituent_mismatch == "basis" and role == "peer_basket" and index == 0 else "gaap"
            horizon = "NTM" if constituent_mismatch == "horizon" and role == "peer_basket" and index == 0 else "FY1"
            if constituent_mismatch == "ticker" and role == "peer_basket" and index == 1:
                ticker = f"{prefix}1"
            forward_eps = 10.0 + index
            multiple = 24.0 + index
            evidence = {
                "ticker": ticker, "accounting_basis": basis,
                "forecast_horizon": horizon, "currency": "USD",
                "as_of": "2026-10-14", "price": forward_eps * multiple,
                "forward_eps": forward_eps,
            }
            constituents.append({
                "ticker": ticker,
                "price": forward_eps * multiple,
                "forward_eps": forward_eps,
                "forward_multiple": multiple,
                "market_cap": 100.0,
                "weight": 0.5,
                "accounting_basis": basis,
                "forecast_horizon": horizon,
                "currency": "USD",
                "as_of": "2026-10-14",
                "source_record_hash": (
                    "0" * 64
                    if constituent_mismatch == "hash" and role == "peer_basket" and index == 0
                    else canonical_record_hash(evidence)
                ),
            })
        relative_raw_rows.append({
            "role": role,
            "symbol": prefix,
            "as_of": "2026-10-14",
            "available_at": "2026-10-14T21:00:00Z",
            "forward_multiple": 24.5 + aggregate_delta,
            "accounting_basis": "gaap",
            "forecast_horizon": "FY1",
            "currency": "USD",
            "unit": "multiple",
            "weighting_method": "equal_weight",
            "constituents": constituents,
        })
    relative_raw = tmp_path / "relative-source.json"
    source_hashes["relative_forward_multiples"] = _raw(relative_raw, "relative_forward_multiples", relative_raw_rows)
    relative_manifest = tmp_path / "relative.json"
    _write_json(
        relative_manifest,
        _manifest(
            "relative_forward_multiples", classification, relative_raw,
            _bound_rows(relative_raw_rows, source_hashes["relative_forward_multiples"]),
            source_available_at="2026-10-14T21:00:00Z",
        ),
    )
    paths["relative_forward_multiples"] = relative_manifest

    filing_raw_rows = [
        {
            "ticker": ticker,
            "filing_date": "2026-08-28" if filing_saturday else "2026-08-27",
            "timing_basis": filing_basis,
            "accepted_at": filing_accepted_at,
            "usable_from_session": filing_usable_from or (
                "2026-08-29" if filing_saturday else "2026-08-28"
            ),
        }
        for ticker in TICKERS
    ]
    filing_raw = tmp_path / "filing-source.json"
    source_hashes["filing_timing"] = _raw(filing_raw, "filing_timing", filing_raw_rows)
    filing_manifest = tmp_path / "filing.json"
    _write_json(
        filing_manifest,
        _manifest("filing_timing", classification, filing_raw,
                  _bound_rows(filing_raw_rows, source_hashes["filing_timing"])),
    )
    paths["filing_timing"] = filing_manifest

    sessions = list(xnys_sessions("2025-01-01", "2026-10-14"))[-200:]
    if weekend_price:
        sessions[-1] = datetime(2026, 10, 10).date()
    price_raw_records = {
        symbol: [
            {"date": session.isoformat(), "adjusted_close": 100.0 + index}
            for index, session in enumerate(sessions)
        ]
        for symbol in (*TICKERS, "QQQ", "SOXX")
    }
    price_raw = tmp_path / "prices-source.json"
    source_hashes["adjusted_price_history"] = _raw(price_raw, "adjusted_price_history", price_raw_records)
    price_rows = {
        symbol: _bound_rows(rows, source_hashes["adjusted_price_history"])
        for symbol, rows in price_raw_records.items()
    }
    price_manifest = tmp_path / "prices.json"
    _write_json(
        price_manifest,
        _manifest(
            "adjusted_price_history", classification, price_raw, price_rows,
            source_available_at="2026-10-14T22:00:00Z",
            adjustment_policy="split_and_cash_dividend_adjusted",
            corporate_action_policy="provider back-adjusted with preserved action log",
            calendar="XNYS",
        ),
    )
    paths["adjusted_price_history"] = price_manifest

    regime_raw_rows = [{
        "as_of": regime_as_of,
        "available_at": regime_available_at,
        "risk_state": "neutral",
        "source_kind": "cross_project_market_proxy",
        "indicators": {"risk_proxy": 0.5},
    }]
    regime_raw = tmp_path / "regime-source.json"
    source_hashes["regime"] = _raw(regime_raw, "regime", regime_raw_rows)
    regime_manifest = tmp_path / "regime.json"
    _write_json(
        regime_manifest,
        _manifest(
            "regime", classification, regime_raw,
            _bound_rows(regime_raw_rows, source_hashes["regime"]),
            source_available_at="2026-10-14T21:00:00Z",
        ),
    )
    paths["regime"] = regime_manifest

    rights_raw_rows = [
        {
            "evidence_source_hash": source_hash,
            "usage_scope": "personal_local_research",
            "storage_allowed": True,
            "derivative_analysis_allowed": True,
            "local_display_allowed": True,
            "public_display_allowed": False,
            "reviewed_at": "2026-10-01T00:00:00Z",
            "expires_at": "2027-10-01T00:00:00Z",
        }
        for source_hash in source_hashes.values()
    ]
    rights_raw = tmp_path / "rights-source.json"
    source_hashes["rights"] = _raw(rights_raw, "rights", rights_raw_rows)
    rights_manifest = tmp_path / "rights.json"
    _write_json(
        rights_manifest,
        _manifest(
            "rights", classification, rights_raw,
            _bound_rows(rights_raw_rows, source_hashes["rights"]),
            source_available_at="2026-10-14T22:30:00Z",
        ),
    )
    paths["rights"] = rights_manifest
    assert set(paths) == set(CONTRACT_ROLES)
    return paths


def _validate(paths: dict[str, Path]) -> dict[str, Any]:
    roots = {path.parent.resolve() for path in paths.values()}
    assert len(roots) == 1
    return validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS,
        actual_evidence_root=next(iter(roots)),
    )


def _requirement(report: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    return next(row for row in report["requirements"] if row["id"] == requirement_id)


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


def test_actual_classification_without_approved_evidence_root_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    report = validate_ready_contracts(paths, decision_time=DECISION_TIME, tickers=TICKERS)
    assert report["promotion_ready"] is False
    assert all("root is not approved" in row["reason"] for row in report["requirements"])


def test_synthetic_fixture_is_distinguished_and_never_promotion_ready(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, classification="synthetic_fixture"))
    assert report["schema_valid"] is True
    assert report["promotion_ready"] is False
    assert all(row["status"] == "fixture_validated" for row in report["requirements"])


def test_raw_bytes_unchanged_but_normalized_eps_changed_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(paths["pit_forward_eps"], lambda value: value["records"][0].update(eps_base=99.0))
    row = _requirement(_validate(paths), "verified_pit_forward_eps")
    assert row["passed"] is False
    assert "does not match parsed source bytes" in row["reason"]


def test_historical_per_using_later_eps_source_is_blocked(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, historical_eps_future=True))
    row = _requirement(report, "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "EPS source <= EPS record < PER observation session" in row["reason"]


def test_weekend_price_is_blocked_by_actual_xnys_calendar(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, weekend_price=True))
    row = _requirement(report, "daily_adjusted_price_history")
    assert row["passed"] is False
    assert "not an XNYS session" in row["reason"]


def test_eps_price_relative_date_mismatch_is_blocked(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, eps_as_of="2026-10-09"))
    row = _requirement(report, "same_date_price_and_regime")
    assert row["passed"] is False
    assert "EPS is stale" in row["reason"]


def test_saturday_cannot_be_conservative_next_session(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, filing_saturday=True))
    row = _requirement(report, "sec_acceptance_or_conservative_next_session")
    assert row["passed"] is False
    assert "not an XNYS session" in row["reason"]


@pytest.mark.parametrize(
    ("accepted_at", "usable_from"),
    [
        ("2026-08-27T18:00:00Z", "2026-08-27"),
        ("2026-08-27T21:00:00Z", "2026-08-28"),
    ],
)
def test_sec_acceptance_before_and_after_close_map_to_correct_session(
    tmp_path: Path, accepted_at: str, usable_from: str
) -> None:
    report = _validate(
        _build_contracts(
            tmp_path,
            filing_basis="sec_acceptance_timestamp",
            filing_accepted_at=accepted_at,
            filing_usable_from=usable_from,
        )
    )
    row = _requirement(report, "sec_acceptance_or_conservative_next_session")
    assert row["passed"] is True


def test_duplicate_relative_roles_are_blocked(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, duplicate_relative_role=True))
    row = _requirement(report, "independent_qqq_soxx_peer_forward_multiples")
    assert row["passed"] is False
    assert "duplicate roles" in row["reason"]


def test_arbitrary_relative_aggregate_change_is_blocked(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, aggregate_delta=1.0))
    row = _requirement(report, "independent_qqq_soxx_peer_forward_multiples")
    assert row["passed"] is False
    assert "constituent recomputation" in row["reason"]


@pytest.mark.parametrize(
    ("mode", "kwargs", "message"),
    [
        ("missing", {}, "regime manifest is missing"),
        ("stale", {"regime_as_of": "2026-10-09"}, "regime is stale"),
        ("future", {"regime_available_at": "2026-10-15T00:00:00Z"}, "later than decision_time"),
    ],
)
def test_regime_missing_stale_or_future_is_blocked(
    tmp_path: Path, mode: str, kwargs: dict[str, str], message: str
) -> None:
    paths = _build_contracts(tmp_path, **kwargs)
    if mode == "missing":
        paths.pop("regime")
    row = _requirement(_validate(paths), "same_date_price_and_regime")
    assert row["passed"] is False
    assert message in row["reason"]


@pytest.mark.parametrize("mismatch", ["ticker", "basis", "horizon", "hash"])
def test_constituent_ticker_basis_or_horizon_mismatch_is_blocked(
    tmp_path: Path, mismatch: str
) -> None:
    report = _validate(_build_contracts(tmp_path, constituent_mismatch=mismatch))
    row = _requirement(report, "independent_qqq_soxx_peer_forward_multiples")
    assert row["passed"] is False
    assert "constituent" in row["reason"]


@pytest.mark.parametrize("invalid_basis", ["unknown", "", "gaap_adjusted_guess"])
def test_unknown_eps_basis_is_blocked(tmp_path: Path, invalid_basis: str) -> None:
    row = _requirement(
        _validate(_build_contracts(tmp_path, eps_basis=invalid_basis)),
        "verified_pit_forward_eps",
    )
    assert row["passed"] is False
    assert "accounting_basis is not approved" in row["reason"]


def test_source_hash_link_break_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    (tmp_path / "pit-forward-eps-source.json").write_text('{"changed":true}', encoding="utf-8")
    row = _requirement(_validate(paths), "verified_pit_forward_eps")
    assert "source_hash mismatch" in row["reason"]


@pytest.mark.parametrize("mode", ["missing", "expired", "insufficient"])
def test_rights_manifest_missing_expired_or_insufficient_is_blocked(
    tmp_path: Path, mode: str
) -> None:
    paths = _build_contracts(tmp_path)
    if mode == "missing":
        paths.pop("rights")
    else:
        raw_path = tmp_path / "rights-source.json"
        raw_value = json.loads(raw_path.read_text(encoding="utf-8"))
        if mode == "expired":
            raw_value["records"][0]["expires_at"] = "2026-10-01T00:00:00Z"
        else:
            raw_value["records"][0]["local_display_allowed"] = False
        _write_json(raw_path, raw_value)
        manifest = json.loads(paths["rights"].read_text(encoding="utf-8"))
        new_hash = _hash(raw_path)
        manifest["source_files"][0]["source_hash"] = new_hash
        manifest["records"] = _bound_rows(raw_value["records"], new_hash)
        _write_json(paths["rights"], manifest)
    row = _requirement(_validate(paths), "decision_display_rights")
    assert row["passed"] is False
    assert row["status"] == "blocked"
