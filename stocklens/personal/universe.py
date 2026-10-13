from __future__ import annotations

from typing import Any


PERSONAL_PILOT_UNIVERSE: dict[str, dict[str, Any]] = {
    "AMAT": {
        "company": "Applied Materials, Inc.",
        "business": "반도체·디스플레이 제조 장비와 서비스",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
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
