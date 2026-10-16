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
    build_candidate_registries,
    canonical_record_hash,
    validate_ready_contracts,
)

DECISION_TIME = "2026-10-14T23:00:00Z"
TICKERS = ("AVGO", "NVDA")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if "normalized_records_sha256" in value:
        value["normalized_records_sha256"] = hashlib.sha256(
            json.dumps(
                value.get("records"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest().upper()
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
    provider_path = path.with_name(f"{path.stem}.provider-native.json")
    _write_json(provider_path, {
        "provider": "unit-test-provider",
        "role": role,
        "source_kind": source_kind,
        "provider_timestamp": captured_at,
        "revision_id": f"unit-test-{role}-revision-1",
        "records": records,
    })
    provider_hash = _hash(provider_path)
    if source_kind == "historical_eps_archive":
        for index, record in enumerate(records):
            record["evidence_status"] = "provider_point_in_time_archive"
            record["pit_proof"] = {
                "type": "provider_point_in_time_archive",
                "provider_raw_sha256": provider_hash,
                "provider_timestamp": record["source_available_at"],
                "vintage_id": f"unit-test-vintage-{index}",
            }
    _write_json(
        path,
        {
            "raw_schema_version": f"{role.replace('_', '-')}-source-v3",
            "source": "deterministic offline contract test evidence",
            "provider": "unit-test-provider",
            "captured_at": captured_at,
            "provider_native": {
                "path": provider_path.name,
                "source_hash": provider_hash,
                "mime_type": "application/json",
                "source_url": f"https://unit-test.invalid/{provider_path.name}",
                "local_collection_path": None,
                "captured_at": captured_at,
                "provider_timestamp": captured_at,
                "revision_id": f"unit-test-{role}-revision-1",
                "vintage_id": None,
                "parser_name": "unit-test-parser",
                "parser_version": "1.0.0",
            },
            "normalized_records_sha256": hashlib.sha256(
                json.dumps(
                    records,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest().upper(),
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
        "contract_version": "same-date-ready-contract-v3",
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
    terms_path = root / "unit-test-terms.txt"
    terms_path.write_text("Offline unit-test terms evidence.", encoding="utf-8")
    policy_input = root / "usage-policy-input.json"
    _write_json(policy_input, {
        "schema_version": "same-date-usage-policy-input-v1",
        "records": [{
            "provider": "unit-test-provider",
            "usage_scope": "personal_local_research",
            "storage_allowed": True,
            "derivative_analysis_allowed": True,
            "local_display_allowed": True,
            "public_display_allowed": False,
            "terms_document_path": "../unit-test-terms.txt",
            "terms_document_sha256": _hash(terms_path),
            "terms_clause_ids": ["unit-test-clause-1"],
            "reviewed_at": "2026-10-01T00:00:00Z",
            "expires_at": "2027-10-01T00:00:00Z",
        }],
    })
    candidates = build_candidate_registries(
        paths,
        actual_evidence_root=root,
        usage_policy_input_path=policy_input,
        output_dir=root / "candidates",
        generated_by="unit-test-candidate-builder",
        generated_at="2026-10-14T22:30:00Z",
    )
    qa_root = root.parent / f"{root.name}-independent-qa"
    qa_root.mkdir(parents=True, exist_ok=True)
    approval_path = qa_root / "approval-receipt.json"
    _write_json(approval_path, {
        "schema_version": "same-date-independent-approval-receipt-v1",
        "status": "approved",
        "approved_by": "independent-unit-test-qa",
        "approved_at": "2026-10-14T22:45:00Z",
        "approved_registry_sha256": {
            "provenance_candidate": candidates["provenance_registry_sha256"],
            "usage_policy_candidate": candidates["usage_policy_registry_sha256"],
        },
        "approval_scope": "personal_local_same_date_research",
        "approval_receipt_id": "QA-UNIT-0001",
        "expires_at": "2027-10-01T00:00:00Z",
        "decision_time": DECISION_TIME,
        "target_tickers": list(TICKERS),
        "operational_eligible": False,
    })
    return {
        "actual_evidence_root": root,
        "provenance_registry_path": Path(candidates["provenance_registry_path"]),
        "usage_policy_registry_path": Path(candidates["usage_policy_registry_path"]),
        "approval_receipt_path": approval_path,
        "approved_approval_receipt_sha256": _hash(approval_path),
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
    assert any("provenance candidate registry is missing" in row["reason"] for row in report["requirements"])


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
    row = _requirement(_validate(paths, approve=False), "verified_pit_forward_eps")
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
    row = _requirement(_validate(paths, approve=mode != "missing"), "same_date_price_and_regime")
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
    else:
        registry_path = Path(approval["usage_policy_registry_path"])
        value = json.loads(registry_path.read_text(encoding="utf-8"))
        if mode == "expired":
            value["records"][0]["expires_at"] = "2026-10-01T00:00:00Z"
        else:
            value["records"][0]["local_display_allowed"] = False
        _write_json(registry_path, value)
    report = validate_ready_contracts(
        paths,
        decision_time=DECISION_TIME,
        tickers=TICKERS,
        **approval,
    )
    row = _requirement(report, "decision_display_rights")
    assert row["passed"] is False
    assert row["status"] == "blocked"


def test_mutated_provenance_candidate_is_blocked_by_independent_receipt(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    registry_path = Path(approval["provenance_registry_path"])
    value = json.loads(registry_path.read_text(encoding="utf-8"))
    value["generated_at"] = "2026-10-14T22:31:00Z"
    _write_json(registry_path, value)
    report = validate_ready_contracts(
        paths,
        decision_time=DECISION_TIME,
        tickers=TICKERS,
        **approval,
    )
    assert report["promotion_ready"] is False
    assert any("provenance_candidate hash mismatch" in row["reason"] for row in report["requirements"])


def test_candidate_generation_never_creates_approval(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    candidate_root = Path(approval["provenance_registry_path"]).parent
    assert not list(candidate_root.glob("*approval*"))
    assert Path(approval["approval_receipt_path"]).parent != candidate_root


def test_independent_approval_receipt_is_required(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    approval["approval_receipt_path"] = None
    approval["approved_approval_receipt_sha256"] = None
    report = validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS, **approval
    )
    assert report["promotion_ready"] is False
    assert report["approval_error"] == "independent approval receipt is missing"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("approved_by", "unit-test-candidate-builder", "cannot approve its own"),
        ("approval_scope", "public_display", "scope is not approved"),
        ("expires_at", "2026-10-01T00:00:00Z", "not valid at decision_time"),
        ("decision_time", "2026-10-13T23:00:00Z", "decision_time does not match"),
        ("target_tickers", ["AVGO"], "target_tickers do not match"),
    ],
)
def test_invalid_independent_approval_receipt_is_blocked(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    receipt_path = Path(approval["approval_receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    _write_json(receipt_path, receipt)
    approval["approved_approval_receipt_sha256"] = _hash(receipt_path)
    report = validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS, **approval
    )
    assert report["promotion_ready"] is False
    assert message in str(report["approval_error"])


def test_untrusted_approval_receipt_hash_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    approval["approved_approval_receipt_sha256"] = "0" * 64
    report = validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS, **approval
    )
    assert report["promotion_ready"] is False
    assert report["approval_error"] == "independent approval receipt SHA-256 mismatch"


def test_approval_receipt_inside_candidate_root_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    source = Path(approval["approval_receipt_path"])
    inside = tmp_path / "approval-receipt.json"
    inside.write_bytes(source.read_bytes())
    approval["approval_receipt_path"] = inside
    approval["approved_approval_receipt_sha256"] = _hash(inside)
    report = validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS, **approval
    )
    assert report["promotion_ready"] is False
    assert "outside the candidate evidence root" in str(report["approval_error"])


def test_provider_native_raw_must_not_equal_normalized_contract(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    source_path = tmp_path / "pit-forward-eps-source.json"
    value = json.loads(source_path.read_text(encoding="utf-8"))
    value["provider_native"]["path"] = source_path.name
    value["provider_native"]["source_hash"] = _hash(source_path)
    _write_json(source_path, value)
    manifest = json.loads(paths["pit_forward_eps"].read_text(encoding="utf-8"))
    manifest["source_files"][0]["source_hash"] = _hash(source_path)
    _write_json(paths["pit_forward_eps"], manifest)
    row = _requirement(_validate(paths, approve=False), "verified_pit_forward_eps")
    assert "provider-native raw and normalized files must differ" in row["reason"]


def test_provider_native_raw_changed_after_approval_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    approval = _approval_args(paths)
    registry_path = Path(approval["provenance_registry_path"])
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    original_path = (registry_path.parent / registry["records"][0]["original_file_path"]).resolve()
    original_path.write_bytes(original_path.read_bytes() + b"\nchanged")
    report = validate_ready_contracts(
        paths, decision_time=DECISION_TIME, tickers=TICKERS, **approval
    )
    assert report["promotion_ready"] is False
    assert any(
        "provider-native raw hash mismatch" in row["reason"]
        or "original_file_sha256" in row["reason"]
        for row in report["requirements"]
    )


def test_approval_cli_does_not_accept_registry_sha_arguments() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "audit_same_date_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "--approved-provenance-registry-sha256" not in source
    assert "--approved-usage-policy-registry-sha256" not in source
    assert "STOCKLENS_SAME_DATE_APPROVAL_RECEIPT_SHA256" in source


def test_historical_availability_unverified_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / "historical-eps-source.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["records"][0]["evidence_status"] = "historical_availability_unverified"
    _write_json(raw_path, raw)
    new_hash = _hash(raw_path)
    manifest = json.loads(paths["company_pit_forward_per"].read_text(encoding="utf-8"))
    manifest["source_files"][1]["source_hash"] = new_hash
    per_raw_path = tmp_path / "company-per-source.json"
    per_raw = json.loads(per_raw_path.read_text(encoding="utf-8"))
    per_raw["records"][0]["underlying_eps_source_hash"] = new_hash
    per_raw["records"][0]["underlying_eps_record_hash"] = canonical_record_hash(raw["records"][0])
    _write_json(per_raw_path, per_raw)
    per_hash = _hash(per_raw_path)
    manifest["source_files"][0]["source_hash"] = per_hash
    manifest["records"] = _bound_rows(per_raw["records"], per_hash)
    _write_json(paths["company_pit_forward_per"], manifest)
    row = _requirement(_validate(paths, approve=False), "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "historical_availability_unverified" in row["reason"]


def test_self_declared_historical_timestamp_without_matching_proof_is_blocked(tmp_path: Path) -> None:
    paths = _build_contracts(tmp_path)
    raw_path = tmp_path / "historical-eps-source.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["records"][0]["pit_proof"]["provider_timestamp"] = "2026-10-14T00:00:00Z"
    _write_json(raw_path, raw)
    new_hash = _hash(raw_path)
    manifest = json.loads(paths["company_pit_forward_per"].read_text(encoding="utf-8"))
    manifest["source_files"][1]["source_hash"] = new_hash
    per_raw_path = tmp_path / "company-per-source.json"
    per_raw = json.loads(per_raw_path.read_text(encoding="utf-8"))
    per_raw["records"][0]["underlying_eps_source_hash"] = new_hash
    per_raw["records"][0]["underlying_eps_record_hash"] = canonical_record_hash(raw["records"][0])
    _write_json(per_raw_path, per_raw)
    per_hash = _hash(per_raw_path)
    manifest["source_files"][0]["source_hash"] = per_hash
    manifest["records"] = _bound_rows(per_raw["records"], per_hash)
    _write_json(paths["company_pit_forward_per"], manifest)
    row = _requirement(_validate(paths, approve=False), "company_pit_forward_per_ledger")
    assert row["passed"] is False
    assert "PIT proof timestamp does not match source availability" in row["reason"]
