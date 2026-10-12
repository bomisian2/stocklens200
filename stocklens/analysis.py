from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd


ANALYSIS_AREAS = ("market", "business", "financial", "outlook", "valuation", "technical")
VALID_STATES = {"verified", "partial", "research_only", "stale", "unavailable", "blocked"}
STATE_LABELS = {
    "verified": "검증 완료",
    "partial": "일부 확보",
    "research_only": "연구 전용",
    "stale": "오래된 자료",
    "unavailable": "자료 없음",
    "blocked": "검증 차단",
}
PROHIBITED_RESEARCH_KEYS = {
    "recommendation",
    "target_price",
    "buy_zone",
    "sell_signal",
    "buy_signal",
    "upside",
    "undervaluation",
}


class AnalysisSchemaError(ValueError):
    pass


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _validate_area(area: dict[str, Any], *, ticker: str, name: str) -> None:
    state = str(area.get("state") or "")
    if state not in VALID_STATES:
        raise AnalysisSchemaError(f"{ticker} {name} has invalid state: {state}")
    if not str(area.get("summary") or "").strip():
        raise AnalysisSchemaError(f"{ticker} {name} requires a summary")
    if state != "verified" and not str(area.get("hold_reason") or "").strip():
        raise AnalysisSchemaError(f"{ticker} {name} requires a hold reason")
    sources = area.get("sources") or []
    if not isinstance(sources, list):
        raise AnalysisSchemaError(f"{ticker} {name} sources must be a list")
    for source in sources:
        if not isinstance(source, dict) or not source.get("label") or not source.get("url"):
            raise AnalysisSchemaError(f"{ticker} {name} source requires label and URL")


def load_analysis_overlays(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("dataset_kind") != "mvp_analysis_overlays":
        raise AnalysisSchemaError("unsupported analysis overlay schema")
    if payload.get("operational_valuation_source") is not False:
        raise AnalysisSchemaError("research overlays cannot be an operational valuation source")

    overlays: dict[str, dict[str, Any]] = {}
    for company in payload.get("companies") or []:
        ticker = str(company.get("ticker") or "").upper()
        if not ticker or ticker in overlays:
            raise AnalysisSchemaError("analysis overlays require unique tickers")
        areas = company.get("areas") or {}
        for name in ANALYSIS_AREAS[1:]:
            if name not in areas:
                raise AnalysisSchemaError(f"{ticker} is missing {name}")
            _validate_area(areas[name], ticker=ticker, name=name)

        research = company.get("research_value")
        if research is not None:
            if research.get("operational_eligible") is not False:
                raise AnalysisSchemaError("research value must be operationally ineligible")
            if research.get("judgement") != "hold":
                raise AnalysisSchemaError("research value judgement must remain hold")
            if research.get("formal_value") is not None or research.get("midpoint") is not None:
                raise AnalysisSchemaError("research value cannot contain a formal value or midpoint")
            if research.get("automatic_price_comparison") is not False:
                raise AnalysisSchemaError("research value cannot compare itself with the market price")
            bounds = research.get("range") or {}
            if not (float(bounds.get("low", 0)) > 0 and float(bounds.get("high", 0)) > float(bounds.get("low", 0))):
                raise AnalysisSchemaError("research value requires positive ordered bounds")
            prohibited = _walk_keys(research) & PROHIBITED_RESEARCH_KEYS
            if prohibited:
                raise AnalysisSchemaError(f"research value contains prohibited keys: {sorted(prohibited)}")
        overlays[ticker] = company
    return overlays


def _empty_area(summary: str, reason: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "as_of": None,
        "summary": summary,
        "hold_reason": reason,
        "sources": [],
        "details": [],
    }


def build_analysis_catalog(
    universe: pd.DataFrame,
    status: dict[str, Any],
    overlays: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    market_state = "verified"
    if status.get("data_state") == "stale":
        market_state = "stale"
    elif status.get("freshness_state") != "verified":
        market_state = "blocked"

    catalog: list[dict[str, Any]] = []
    market_session_date = str(status.get("market_session_date") or "")
    for row in universe.to_dict(orient="records"):
        ticker = str(row["ticker"]).upper()
        company = {
            "ticker": ticker,
            "company_name": str(row["name"]),
            "rank": int(row["rank"]),
            "sector": str(row.get("sector") or "Unknown"),
            "industry": str(row.get("industry") or "Unknown"),
            "country": str(row.get("country") or "Unknown"),
            "market_cap": float(row["market_cap"]),
            "price": float(row["price"]),
            "change_percent": float(row.get("change_percent") or 0),
            "market_time_utc": str(row["market_time_utc"]),
            "market_session_date": market_session_date or str(row["market_time_utc"])[:10],
            "collected_at_utc": str(row["collected_at_utc"]),
            "operational_eligible": False,
            "official_financial_periods": 0,
            "areas": {
                "market": {
                    "state": market_state,
                    "as_of": str(row["market_time_utc"]),
                    "summary": "검증된 Top 200 시장 스냅샷" if market_state == "verified" else "시장 데이터 검증 보류",
                    "hold_reason": "" if market_state == "verified" else "시장 최신성 또는 품질 게이트 미통과",
                    "sources": [
                        {"label": str(row.get("source") or "시장 데이터"), "url": "https://finance.yahoo.com/"},
                        {"label": str(row.get("metadata_source") or "독립 검증"), "url": "https://www.nasdaq.com/market-activity/stocks/screener"},
                    ],
                    "details": [],
                },
                "business": _empty_area("사업 경쟁력 분석 전", "출처가 연결된 사업 분석 자료가 없습니다."),
                "financial": _empty_area("공식 재무자료 미연결", "검증된 FinancialPeriod가 없습니다."),
                "outlook": _empty_area("미래 전망 자료 미연결", "공식 가이던스 또는 허용된 컨센서스가 없습니다."),
                "valuation": _empty_area("정식 가치평가 없음", "검증된 재무·전망·모델 입력이 부족합니다."),
                "technical": _empty_area("종목별 기술 분석 미연결", "PR #3의 종목별 운영 결과를 가져오지 않았습니다."),
            },
            "risks": [],
            "invalidation_conditions": [],
            "research_value": None,
        }
        if ticker in overlays:
            overlay = deepcopy(overlays[ticker])
            for key in ("operational_eligible", "official_financial_periods", "risks", "invalidation_conditions", "research_value"):
                if key in overlay:
                    company[key] = overlay[key]
            company["areas"].update(overlay["areas"])
        catalog.append(company)

    if len(catalog) != 200:
        raise AnalysisSchemaError(f"analysis catalog must contain 200 companies, got {len(catalog)}")
    return catalog


def catalog_table(catalog: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for company in catalog:
        rows.append({
            "rank": company["rank"],
            "ticker": company["ticker"],
            "company_name": company["company_name"],
            "sector": company["sector"],
            "price": company["price"],
            "market_date": company["market_session_date"],
            "business_state": company["areas"]["business"]["state"],
            "financial_state": company["areas"]["financial"]["state"],
            "valuation_state": company["areas"]["valuation"]["state"],
            "technical_state": company["areas"]["technical"]["state"],
        })
    return pd.DataFrame(rows).sort_values("rank").reset_index(drop=True)


def find_company(catalog: list[dict[str, Any]], ticker: str) -> dict[str, Any]:
    for company in catalog:
        if company["ticker"] == ticker.upper():
            return company
    raise AnalysisSchemaError(f"ticker not in catalog: {ticker}")


def load_signal_research(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("dataset_kind") != "signal_research_summary":
        raise AnalysisSchemaError("unsupported signal research schema")
    if payload.get("operational_eligible") is not False:
        raise AnalysisSchemaError("signal research cannot be operationally eligible")
    if int(payload.get("independent_validated_count", -1)) != 0:
        raise AnalysisSchemaError("MVP snapshot expects zero independently validated strategies")
    if int(payload.get("active_signal_count", -1)) != 0:
        raise AnalysisSchemaError("research snapshot cannot publish active signals")
    if _walk_keys(payload) & {"recommendation", "buy_signal", "sell_signal"}:
        raise AnalysisSchemaError("signal research contains an operational recommendation")
    return payload


def coverage_metrics(catalog: list[dict[str, Any]], signal_summary: dict[str, Any]) -> dict[str, int]:
    return {
        "company_count": len(catalog),
        "official_financial_companies": sum(int(row.get("official_financial_periods", 0)) > 0 for row in catalog),
        "research_value_companies": sum(row.get("research_value") is not None for row in catalog),
        "formal_valuation_companies": sum(
            row["areas"]["valuation"]["state"] == "verified" and bool(row.get("operational_eligible"))
            for row in catalog
        ),
        "independent_signal_count": int(signal_summary["independent_validated_count"]),
        "active_signal_count": int(signal_summary["active_signal_count"]),
    }
