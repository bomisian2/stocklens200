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


def _raw(
    path: Path,
    role: str,
    records: Any,
    *,
    captured_at: str = "2026-10-14T22:00:00Z",
    source_kind: str | None = None,
) -> str:
    _write_json(
        path,
        {
            "raw_schema_version": f"{role.replace('_', '-')}-source-v2",
            "source": "deterministic offline contract test evidence",
            "provider": "unit-test-provider",
            "captured_at": captured_at,
            **({"source_kind": source_kind} if source_kind else {}),
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
        "schema_version": f"{role.replace('_', '-')}-v2",
        "contract_version": "same-date-ready-contract-v2",
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
    classification: str = "local_evidence_candidate",
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
    price_available_mode: str = "valid",
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
            "provider": "unit-test-provider",
            "usage_scope": "personal_local_research",
            "eps_low": 9.0,
            "eps_base": 10.0,
            "eps_high": 11.0,
        }
        for ticker in TICKERS
    ]
    eps_raw = tmp_path / "pit-forward-eps-source.json"
    source_hashes["pit_forward_eps"] = _raw(
        eps_raw,
        "pit_forward_eps",
        eps_raw_rows,
        captured_at="2026-10-13T20:00:00Z",
    )
    eps_manifest = tmp_path / "pit-forward-eps.json"
    _write_json(
        eps_manifest,
        _manifest(
            "pit_forward_eps",
            classification,
            eps_raw,
            _bound_rows(eps_raw_rows, source_hashes["pit_forward_eps"], canonical=True),
            source_available_at="2026-10-13T20:00:00Z",
        ),
    )
    paths["pit_forward_eps"] = eps_manifest

    # Historical PER rows reference a separate preserved point-in-time EPS archive.
    history_sessions = xnys_sessions("2023-01-03", "2026-09-30")[::45][:20]
    per_raw_rows: list[dict[str, Any]] = []
    historical_eps_rows: list[dict[str, Any]] = []
    for ticker in TICKERS:
        for index, session in enumerate(history_sessions):
            session_open, session_close = xnys_session_bounds(session)
            eps_available = session_open - timedelta(hours=1)
            eps_source_available = eps_available - timedelta(hours=1)
            if historical_eps_future and ticker == TICKERS[0] and index == 0:
                eps_as_of = datetime(2030, 1, 2, tzinfo=timezone.utc).date()
            else:
                eps_as_of = session
            forward_eps = 8.0 + index / 10
            forward_per = 20.0 + index / 10
            historical_eps = {
                "ticker": ticker,
                "accounting_basis": "gaap",
                "forecast_horizon": "FY1",
                "currency": "USD",
                "unit": "currency_per_diluted_share",
                "as_of": eps_as_of.isoformat(),
                "source_available_at": eps_source_available.isoformat(),
                "available_at": eps_available.isoformat(),
                "eps_base": forward_eps,
                "provider": "unit-test-provider",
            }
            historical_eps_rows.append(historical_eps)
    historical_eps_raw = tmp_path / "historical-eps-source.json"
    source_hashes["historical_eps"] = _raw(
        historical_eps_raw,
        "company_pit_forward_per",
        historical_eps_rows,
        captured_at="2026-10-14T22:00:00Z",
        source_kind="historical_eps_archive",
    )
    for ticker in TICKERS:
        for index, session in enumerate(history_sessions):
            _, session_close = xnys_session_bounds(session)
            historical_eps = historical_eps_rows[
                TICKERS.index(ticker) * len(history_sessions) + index
            ]
            forward_eps = float(historical_eps["eps_base"])
            forward_per = 20.0 + index / 10
            per_raw_rows.append({
                "ticker": ticker,
                "as_of": session.isoformat(),
                "available_at": session_close.isoformat(),
                "price": forward_eps * forward_per,
                "forward_eps": forward_eps,
                "forward_per": forward_per,
                "underlying_eps_source_hash": source_hashes["historical_eps"],
                "underlying_eps_record_hash": canonical_record_hash(historical_eps),
                "provider": "unit-test-provider",
                "accounting_basis": "gaap",
                "forecast_horizon": "FY1",
                "currency": "USD",
                "unit": "multiple",
                "split_adjustment_policy": "price_and_eps_split_adjusted",
            })
    per_raw = tmp_path / "company-per-source.json"
    source_hashes["company_pit_forward_per"] = _raw(
        per_raw,
        "company_pit_forward_per",
        per_raw_rows,
        captured_at="2026-10-14T22:00:00Z",
        source_kind="per_ledger",
    )
    per_rows = _bound_rows(per_raw_rows, source_hashes["company_pit_forward_per"])
    per_manifest = tmp_path / "company-per.json"
    per_manifest_value = _manifest(
        "company_pit_forward_per",
        classification,
        per_raw,
        per_rows,
        source_available_at="2026-10-14T22:00:00Z",
    )
    per_manifest_value["source_files"].append({
        "path": historical_eps_raw.name,
        "source_hash": source_hashes["historical_eps"],
        "available_at": "2026-10-14T22:00:00Z",
    })
    _write_json(per_manifest, per_manifest_value)
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
    source_hashes["relative_forward_multiples"] = _raw(
        relative_raw,
        "relative_forward_multiples",
        relative_raw_rows,
        captured_at="2026-10-14T21:00:00Z",
    )
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
    source_hashes["filing_timing"] = _raw(
        filing_raw,
        "filing_timing",
        filing_raw_rows,
        captured_at="2026-10-13T18:00:00Z",
    )
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
    price_raw_records: dict[str, list[dict[str, Any]]] = {}
    for symbol in (*TICKERS, "QQQ", "SOXX"):
        price_raw_records[symbol] = []
        for index, session in enumerate(sessions):
            if weekend_price and index == len(sessions) - 1:
                session_close = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
            else:
                _, session_close = xnys_session_bounds(session)
            available_at = session_close
            if index == len(sessions) - 1 and price_available_mode == "before_open":
                available_at = session_close - timedelta(hours=8)
            elif index == len(sessions) - 1 and price_available_mode == "one_second_before":
                available_at = session_close - timedelta(seconds=1)
            elif index == len(sessions) - 1 and price_available_mode == "next_session":
                next_session = xnys_sessions(session + timedelta(days=1), session + timedelta(days=7))[0]
                _, available_at = xnys_session_bounds(next_session)
            price_raw_records[symbol].append({
                "price_basis": "adjusted_close",
                "session": session.isoformat(),
                "session_close_at": session_close.isoformat(),
                "available_at": available_at.isoformat(),
                "adjusted_close": 100.0 + index,
                "corporate_action_basis": "split_and_cash_dividend_adjusted",
            })
    price_raw = tmp_path / "prices-source.json"
    source_hashes["adjusted_price_history"] = _raw(price_raw, "adjusted_price_history", price_raw_records)
    price_rows = {
        symbol: [
            {
                **row,
                "source_hash": source_hashes["adjusted_price_history"],
                "original_source_hash": source_hashes["adjusted_price_history"],
            }
            for row in rows
        ]
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
    source_hashes["regime"] = _raw(
        regime_raw,
        "regime",
        regime_raw_rows,
        captured_at="2026-10-14T21:00:00Z",
    )
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

    assert set(paths) == set(CONTRACT_ROLES)
    return paths


def _approval_args(paths: dict[str, Path]) -> dict[str, Any]:
    roots = {path.parent.resolve() for path in paths.values()}
    assert len(roots) == 1
    root = next(iter(roots))
    source_paths: dict[str, Path] = {}
    for manifest_path in paths.values():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["source_files"]:
            source_path = (manifest_path.parent / item["path"]).resolve()
            source_paths[_hash(source_path)] = source_path
    provenance_path = root / "operator-provenance-registry.json"
    provenance_records = []
    for source_hash, source_path in sorted(source_paths.items()):
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        provenance_records.append({
            "provider": raw["provider"],
            "original_source_url": f"https://unit-test.invalid/{source_path.name}",
            "captured_at": raw["captured_at"],
            "original_mime_type": "application/json",
            "original_file_sha256": source_hash,
            "collector_version": "unit-test-collector-v1",
            "parser_version": "unit-test-parser-v1",
            "original_file_path": source_path.name,
            "approval_receipt": "unit-test-operator-receipt",
        })
    _write_json(provenance_path, {
        "schema_version": "same-date-provenance-registry-v1",
        "records": provenance_records,
    })
    terms_path = root / "unit-test-terms.txt"
    terms_path.write_text("Offline unit-test terms evidence.", encoding="utf-8")
    policy_path = root / "operator-usage-policy-registry.json"
    _write_json(policy_path, {
        "schema_version": "same-date-usage-policy-registry-v1",
        "records": [{
            "provider": "unit-test-provider",
            "usage_scope": "personal_local_research",
            "storage_allowed": True,
            "derivative_analysis_allowed": True,
            "local_display_allowed": True,
            "public_display_allowed": False,
            "terms_document_path": terms_path.name,
            "terms_document_sha256": _hash(terms_path),
            "terms_clause_ids": ["unit-test-clause-1"],
            "reviewed_at": "2026-10-01T00:00:00Z",
            "expires_at": "2027-10-01T00:00:00Z",
            "approval_receipt": "unit-test-rights-review-receipt",
        }],
    })
    return {
        "actual_evidence_root": root,
        "provenance_registry_path": provenance_path,
        "approved_provenance_registry_sha256": _hash(provenance_path),
        "usage_policy_registry_path": policy_path,
        "approved_usage_policy_registry_sha256": _hash(policy_path),
    }


def _validate(paths: dict[str, Path], *, approve: bool = True) -> dict[str, Any]:
    approval = _approval_args(paths) if approve else {"actual_evidence_root": next(iter(paths.values())).parent}
    return validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS,
        **approval,
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


def test_candidate_classification_without_pinned_approval_registries_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    report = _validate(paths, approve=False)
    assert report["promotion_ready"] is False
    assert all(row["passed"] is False for row in report["requirements"])
    assert any("provenance registry is missing" in row["reason"] for row in report["requirements"])


def test_manifest_cannot_self_declare_actual_evidence(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, classification="actual_local_evidence"))
    assert report["promotion_ready"] is False
    assert all(row["passed"] is False for row in report["requirements"])
    assert any("evidence_classification is not approved" in row["reason"] for row in report["requirements"])


def test_v1_contract_cannot_be_relabelled_as_v2(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    manifest = json.loads(paths["pit_forward_eps"].read_text(encoding="utf-8"))
    manifest["contract_version"] = "same-date-ready-contract-v1"
    _write_json(paths["pit_forward_eps"], manifest)
    row = _requirement(_validate(paths), "verified_pit_forward_eps")
    assert row["passed"] is False
    assert "contract_version is not approved" in row["reason"]


def test_synthetic_fixture_is_distinguished_and_never_promotion_ready(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, classification="synthetic_fixture"))
    assert report["schema_valid"] is True
    assert report["promotion_ready"] is False
    assert all(row["status"] == "fixture_validated" for row in report["requirements"])


def test_raw_bytes_unchanged_but_normalized_eps_changed_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(paths["pit_forward_eps"], lambda value: value["records"][0].update(eps_base=99.0))
    row = _requirement(_validate(paths, approve=False), "verified_pit_forward_eps")
    assert row["passed"] is False
    assert "does not match parsed source bytes" in row["reason"]


def test_manifest_source_availability_must_match_raw_capture(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    _change(
        paths["pit_forward_eps"],
        lambda value: value["source_files"][0].update(available_at="2026-10-13T19:59:59Z"),
    )
    row = _requirement(_validate(paths), "verified_pit_forward_eps")
    assert row["passed"] is False
    assert "does not match raw captured_at" in row["reason"]


def test_historical_per_using_later_eps_source_is_blocked(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, historical_eps_future=True))
    row = _requirement(report, "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "eps_record.as_of is later than PER observation session" in row["reason"]


def test_historical_per_future_as_of_with_earlier_available_at_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / "historical-eps-source.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["records"][0]["as_of"] = "2030-01-02"
    _write_json(raw_path, raw)
    manifest = json.loads(paths["company_pit_forward_per"].read_text(encoding="utf-8"))
    old_hash = manifest["source_files"][1]["source_hash"]
    new_hash = _hash(raw_path)
    manifest["source_files"][1]["source_hash"] = new_hash
    per_raw_path = tmp_path / "company-per-source.json"
    per_raw = json.loads(per_raw_path.read_text(encoding="utf-8"))
    per_raw["records"][0]["underlying_eps_source_hash"] = new_hash
    per_raw["records"][0]["underlying_eps_record_hash"] = canonical_record_hash(raw["records"][0])
    _write_json(per_raw_path, per_raw)
    new_per_hash = _hash(per_raw_path)
    manifest["source_files"][0]["source_hash"] = new_per_hash
    manifest["records"] = _bound_rows(per_raw["records"], new_per_hash)
    _write_json(paths["company_pit_forward_per"], manifest)
    assert old_hash != new_hash
    row = _requirement(_validate(paths), "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "eps_record.as_of is later" in row["reason"]


def test_historical_per_nonexistent_eps_source_hash_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / "company-per-source.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["records"][0]["underlying_eps_source_hash"] = "0" * 64
    _write_json(raw_path, raw)
    manifest = json.loads(paths["company_pit_forward_per"].read_text(encoding="utf-8"))
    new_hash = _hash(raw_path)
    manifest["source_files"][0]["source_hash"] = new_hash
    manifest["records"] = _bound_rows(raw["records"], new_hash)
    _write_json(paths["company_pit_forward_per"], manifest)
    row = _requirement(_validate(paths), "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "historical EPS source/hash link does not exist" in row["reason"]


def test_weekend_price_is_blocked_by_actual_xnys_calendar(tmp_path: Path) -> None:
    report = _validate(_build_contracts(tmp_path, weekend_price=True))
    row = _requirement(report, "daily_adjusted_price_history")
    assert row["passed"] is False
    assert "not an XNYS session" in row["reason"]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("before_open", "before XNYS session close"),
        ("one_second_before", "before XNYS session close"),
        ("next_session", "later than decision_time"),
    ],
)
def test_close_availability_must_follow_same_session_close(
    tmp_path: Path, mode: str, message: str
) -> None:
    row = _requirement(
        _validate(_build_contracts(tmp_path, price_available_mode=mode)),
        "daily_adjusted_price_history",
    )
    assert row["passed"] is False
    assert message in row["reason"]


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


@pytest.mark.parametrize(
    ("role", "source_name", "requirement_id"),
    [
        (
            "relative_forward_multiples",
            "relative-source.json",
            "independent_qqq_soxx_peer_forward_multiples",
        ),
        ("regime", "regime-source.json", "same_date_price_and_regime"),
    ],
)
def test_record_availability_cannot_be_later_than_source_capture(
    tmp_path: Path, role: str, source_name: str, requirement_id: str
) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / source_name
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["records"][0]["available_at"] = "2026-10-14T21:30:00Z"
    _write_json(raw_path, raw)
    manifest = json.loads(paths[role].read_text(encoding="utf-8"))
    new_hash = _hash(raw_path)
    manifest["source_files"][0]["source_hash"] = new_hash
    manifest["records"] = _bound_rows(raw["records"], new_hash)
    _write_json(paths[role], manifest)
    row = _requirement(_validate(paths), requirement_id)
    assert row["passed"] is False
    assert "captured before record availability" in row["reason"]


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
    row = _requirement(_validate(paths, approve=False), "verified_pit_forward_eps")
    assert "source_hash mismatch" in row["reason"]


@pytest.mark.parametrize("mode", ["missing", "expired", "insufficient"])
def test_usage_policy_registry_missing_expired_or_insufficient_is_blocked(
    tmp_path: Path, mode: str
) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    if mode == "missing":
        approval["usage_policy_registry_path"] = None
        approval["approved_usage_policy_registry_sha256"] = None
    else:
        registry_path = Path(approval["usage_policy_registry_path"])
        value = json.loads(registry_path.read_text(encoding="utf-8"))
        if mode == "expired":
            value["records"][0]["expires_at"] = "2026-10-01T00:00:00Z"
        else:
            value["records"][0]["local_display_allowed"] = False
        _write_json(registry_path, value)
        approval["approved_usage_policy_registry_sha256"] = _hash(registry_path)
    report = validate_ready_contracts(
        paths,
        decision_time=DECISION_TIME,
        tickers=TICKERS,
        **approval,
    )
    row = _requirement(report, "decision_display_rights")
    assert row["passed"] is False
    assert row["status"] == "blocked"


def test_unpinned_or_mutated_provenance_registry_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    registry_path = Path(approval["provenance_registry_path"])
    value = json.loads(registry_path.read_text(encoding="utf-8"))
    value["records"][0]["approval_receipt"] = "tampered"
    _write_json(registry_path, value)
    report = validate_ready_contracts(
        paths,
        decision_time=DECISION_TIME,
        tickers=TICKERS,
        **approval,
    )
    assert report["promotion_ready"] is False
    assert any("provenance registry SHA-256 mismatch" in row["reason"] for row in report["requirements"])
