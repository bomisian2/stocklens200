from __future__ import annotations

from dataclasses import replace

from stocklens.providers import (
    JsonHttpClient,
    UniverseRecord,
    YahooUniverseProvider,
    is_company_security,
    issuer_key,
    rank_companies,
)


def record(ticker: str, name: str, cap: float, volume: float = 1) -> UniverseRecord:
    return UniverseRecord(
        ticker=ticker,
        name=name,
        sector="Technology",
        industry="Software",
        country="United States",
        market_cap=cap,
        price=100,
        change_percent=1,
        volume=volume,
        as_of="2026-01-01T00:00:00+00:00",
        source="test",
        issuer_key=issuer_key(name, ticker),
    )


def test_rejects_non_company_securities() -> None:
    assert is_company_security("AAPL", "Apple Inc. Common Stock", 1_000)
    assert is_company_security("TSM", "Taiwan Semiconductor ADR", 1_000)
    assert not is_company_security("ABC-W", "ABC Corp Warrant", 1_000)
    assert not is_company_security("XYZ", "XYZ 6% Preferred Stock", 1_000)
    assert not is_company_security("ETF", "Example Exchange Traded Fund", 1_000)


def test_rank_collapses_share_classes_and_keeps_more_liquid_listing() -> None:
    class_a = record("GOOGL", "Alphabet Inc. Class A Common Stock", 2_000, volume=20)
    class_c = replace(record("GOOG", "Alphabet Inc. Class C Common Stock", 2_000), volume=30)
    other = record("MSFT", "Microsoft Corporation Common Stock", 3_000)
    ranked = rank_companies([class_a, class_c, other], limit=2)
    assert [row.ticker for row in ranked] == ["MSFT", "GOOG"]


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def get(self, *args, **kwargs) -> FakeResponse:
        self.calls += 1
        return next(self.responses)


def test_http_client_retries_403_then_succeeds() -> None:
    session = FakeSession([FakeResponse(403), FakeResponse(403), FakeResponse(200, {"ok": True})])
    sleeps: list[float] = []
    client = JsonHttpClient(session=session, attempts=3, backoff_seconds=0.5, sleep=sleeps.append)
    assert client.get("https://example.test", params={}, headers={}) == {"ok": True}
    assert session.calls == 3
    assert sleeps == [0.5, 1.0]


def test_yahoo_retries_incomplete_payload() -> None:
    calls = 0

    def fake_screen(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"quotes": []}
        return {
            "total": 200,
            "quotes": [
                {
                    "symbol": f"Y{i:03d}",
                    "longName": f"Yahoo Company {i}",
                    "marketCap": 1_000_000 - i,
                    "regularMarketPrice": 10,
                    "regularMarketTime": 1_700_000_000,
                }
                for i in range(200)
            ],
        }

    provider = YahooUniverseProvider(screen=fake_screen, attempts=2, backoff_seconds=0, sleep=lambda _: None)
    assert len(provider.fetch().records) == 200
    assert calls == 2
