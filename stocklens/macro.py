from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree

import requests


TREASURY_XML_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"


class MacroDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class RiskFreeRate:
    as_of: str
    tenor: str
    rate: float
    source: str
    source_url: str


def fetch_ten_year_rate(
    *,
    year: int | None = None,
    as_of_date: date | None = None,
    session: requests.Session | None = None,
) -> RiskFreeRate:
    as_of_date = as_of_date or date.today()
    year = year or as_of_date.year
    response = (session or requests.Session()).get(
        TREASURY_XML_URL,
        params={"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(year)},
        headers={"User-Agent": "StockLens200/2.0 bomisian2@users.noreply.github.com"},
        timeout=(5, 45),
    )
    if response.status_code != 200:
        raise MacroDataError(f"Treasury request failed (HTTP {response.status_code})")
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise MacroDataError("Treasury XML was invalid") from exc
    observations: list[tuple[str, float]] = []
    for entry in root.iter():
        children = list(entry)
        if not children:
            continue
        values: dict[str, str] = {}
        for child in children:
            key = child.tag.split("}")[-1]
            if child.text:
                values[key] = child.text.strip()
            for nested in child.iter():
                nested_key = nested.tag.split("}")[-1]
                if nested.text:
                    values[nested_key] = nested.text.strip()
        date_value = next((value for key, value in values.items() if "NEW_DATE" in key.upper()), None)
        rate_value = next((value for key, value in values.items() if re.search(r"BC_10YEAR$", key.upper())), None)
        if not date_value or not rate_value:
            continue
        try:
            observed_date = date.fromisoformat(date_value[:10])
            if observed_date <= as_of_date:
                observations.append((observed_date.isoformat(), float(rate_value) / 100.0))
        except ValueError:
            continue
    if not observations:
        raise MacroDataError("Treasury XML contained no 10-year observations")
    as_of, rate = max(observations, key=lambda item: item[0])
    if not 0 < rate < 0.25:
        raise MacroDataError("Treasury 10-year rate was outside the allowed range")
    return RiskFreeRate(
        as_of=as_of,
        tenor="10Y",
        rate=rate,
        source="us_treasury_daily_par_yield_curve",
        source_url=(
            f"{TREASURY_XML_URL}?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
        ),
    )

