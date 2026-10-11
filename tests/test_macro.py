from __future__ import annotations

from datetime import date

from stocklens.macro import fetch_ten_year_rate


class _Response:
    status_code = 200
    content = b"""<?xml version='1.0' encoding='utf-8'?>
    <feed xmlns:d='urn:test'>
      <entry><content><properties><d:NEW_DATE>2025-01-02T00:00:00</d:NEW_DATE><d:BC_10YEAR>4.50</d:BC_10YEAR></properties></content></entry>
      <entry><content><properties><d:NEW_DATE>2025-01-03T00:00:00</d:NEW_DATE><d:BC_10YEAR>4.60</d:BC_10YEAR></properties></content></entry>
    </feed>"""


class _Session:
    def get(self, *args, **kwargs):
        return _Response()


def test_treasury_rate_is_limited_to_valuation_date() -> None:
    result = fetch_ten_year_rate(year=2025, as_of_date=date(2025, 1, 2), session=_Session())
    assert result.as_of == "2025-01-02"
    assert result.rate == 0.045

