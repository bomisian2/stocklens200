from __future__ import annotations

from typing import Any


PERSONAL_PILOT_UNIVERSE: dict[str, dict[str, Any]] = {
    "AMAT": {
        "company": "Applied Materials, Inc.",
        "business": "반도체·디스플레이 제조 장비와 서비스",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "share_count_evidence": {
            "value_millions": 793.597443,
            "kind": "common_stock_outstanding",
            "as_of": "2026-07-26",
            "published_at": "2026-08-20",
            "source_url": "https://ir.appliedmaterials.com/node/29581/xbrl-viewer",
            "source_label": "Applied Materials official IR XBRL Form 10-Q cover page",
            "source_integrity": "official_read_only_unhashed",
            "classes": "single listed common-stock class",
        },
    },
    "AVGO": {
        "company": "Broadcom Inc.",
        "business": "반도체와 VMware 기반 인프라 소프트웨어",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
    },
    "QCOM": {
        "company": "QUALCOMM Incorporated",
        "business": "무선통신 칩셋·특허 라이선스·자동차 및 IoT",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
    },
    "GOOGL": {
        "company": "Alphabet Inc.",
        "business": "검색·광고, YouTube, Cloud와 AI 플랫폼",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
    },
    "NVDA": {
        "company": "NVIDIA Corporation",
        "business": "AI 가속기, 데이터센터 컴퓨팅과 소프트웨어 플랫폼",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "partial",
        "financial_source_reason": "공식 10-Q의 최근 분기·반기만 확보; FY2026 원문 미연결로 TTM 미산출",
    },
    "MSFT": {
        "company": "Microsoft Corporation",
        "business": "클라우드, 생산성 소프트웨어, Windows와 게임",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "share_count_evidence": {
            "value_millions": 7427.0,
            "kind": "common_stock_outstanding",
            "as_of": "2026-06-30",
            "published_at": "2026-07-29T16:05:00-04:00",
            "source_url": "https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast",
            "source_label": "Microsoft official FY2026 Form 10-K financial statements",
            "source_integrity": "official_hash_pinned_ir",
            "classes": "common stock",
        },
    },
    "MU": {
        "company": "Micron Technology, Inc.",
        "business": "DRAM·NAND 메모리 및 스토리지",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "blocked",
        "financial_source_reason": "공식 IR 원문이 자동 접근 환경에서 Cloudflare 정책 응답으로 차단됨; 우회·재시도 안 함",
    },
    "AMD": {
        "company": "Advanced Micro Devices, Inc.",
        "business": "데이터센터·PC CPU, GPU 및 적응형 컴퓨팅",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
    },
    "TSM": {
        "company": "Taiwan Semiconductor Manufacturing Company Limited",
        "business": "첨단 반도체 파운드리",
        "accounting_standard": "TIFRS",
        "currency": "TWD",
        "financial_source_state": "blocked",
        "financial_source_reason": "공식 IR PDF가 HTTP 403; TIFRS·TWD·ADR을 US GAAP/USD로 강제 변환하지 않음",
    },
    "LRCX": {
        "company": "Lam Research Corporation",
        "business": "식각·증착 중심 반도체 제조 장비와 서비스",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
    },
}


def pilot_tickers() -> tuple[str, ...]:
    return tuple(PERSONAL_PILOT_UNIVERSE)
