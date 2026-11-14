from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IRDocument:
    ticker: str
    key: str
    url: str
    published_at: str
    period_end: str
    media_type: str
    issuer: str
    distribution: str


PILOT_IR_DOCUMENTS: dict[str, tuple[IRDocument, IRDocument]] = {
    "AMAT": (
        IRDocument(
            ticker="AMAT",
            key="fy2025",
            url=(
                "https://rss.globenewswire.com/news-release/2025/11/13/3187810/0/en/"
                "applied-materials-announces-fourth-quarter-and-fiscal-year-2025-results.html"
            ),
            published_at="2025-11-13T16:01:00-05:00",
            period_end="2025-10-26",
            media_type="text/html",
            issuer="Applied Materials, Inc.",
            distribution="GlobeNewswire issuer release",
        ),
        IRDocument(
            ticker="AMAT",
            key="q3fy2026",
            url=(
                "https://rss.globenewswire.com/news-release/2026/08/13/3344890/0/en/"
                "applied-materials-announces-third-quarter-2026-results.html"
            ),
            published_at="2026-08-13T16:01:00-04:00",
            period_end="2026-07-26",
            media_type="text/html",
            issuer="Applied Materials, Inc.",
            distribution="GlobeNewswire issuer release",
        ),
    ),
    "AVGO": (
        IRDocument(
            ticker="AVGO",
            key="fy2025",
            url=(
                "https://www.prnewswire.com/news-releases/"
                "broadcom-inc-announces-fourth-quarter-and-fiscal-year-2025-financial-"
                "results-and-quarterly-dividend-302639606.html"
            ),
            published_at="2025-12-11T16:15:00-05:00",
            period_end="2025-11-02",
            media_type="text/html",
            issuer="Broadcom Inc.",
            distribution="PR Newswire issuer release",
        ),
        IRDocument(
            ticker="AVGO",
            key="q3fy2026",
            url=(
                "https://www.prnewswire.com/news-releases/"
                "broadcom-inc-announces-third-quarter-fiscal-year-2026-financial-results-"
                "and-quarterly-dividend-302868129.html"
            ),
            published_at="2026-09-02T16:15:00-04:00",
            period_end="2026-08-02",
            media_type="text/html",
            issuer="Broadcom Inc.",
            distribution="PR Newswire issuer release",
        ),
    ),
    "QCOM": (
        IRDocument(
            ticker="QCOM",
            key="fy2025",
            url=(
                "https://s204.q4cdn.com/645488518/files/doc_financials/2025/q4/"
                "FY2025-4th-Quarter-Earnings-Release.pdf"
            ),
            published_at="2025-11-05T16:00:00-05:00",
            period_end="2025-09-28",
            media_type="application/pdf",
            issuer="QUALCOMM Incorporated",
            distribution="Qualcomm official IR CDN",
        ),
        IRDocument(
            ticker="QCOM",
            key="q3fy2026",
            url=(
                "https://s204.q4cdn.com/645488518/files/doc_financials/2026/q3/"
                "FY2026-3rd-Quarter-Earnings-Release.pdf"
            ),
            published_at="2026-07-29T16:00:00-04:00",
            period_end="2026-06-28",
            media_type="application/pdf",
            issuer="QUALCOMM Incorporated",
            distribution="Qualcomm official IR CDN",
        ),
    ),
}
