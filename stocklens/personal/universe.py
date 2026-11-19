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
            "published_at": "20260820160146",
            "filing_date": "2026-08-20",
            "accession_number": "0001628280-26-058235",
            "source_url": "https://ir.appliedmaterials.com/static-files/14c1d32a-cac2-4c39-b895-f33dca70e1ef",
            "source_label": "Applied Materials official IR Form 10-Q cover page",
            "source_integrity": "official_read_only_unhashed",
            "classes": "single listed common-stock class",
            "evidence_note": "10-Q cover: number of common shares outstanding as of the fiscal quarter end",
        },
    },
    "AVGO": {
        "company": "Broadcom Inc.",
        "business": "반도체와 VMware 기반 인프라 소프트웨어",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "share_count_evidence": {
            "value_millions": 4773.629865,
            "kind": "common_stock_outstanding",
            "as_of": "2026-08-28",
            "published_at": "20260909175642",
            "filing_date": "2026-09-10",
            "accession_number": "0001730168-26-000080",
            "source_url": "https://investors.broadcom.com/static-files/96641754-401f-4090-a4ab-210728c83a28",
            "source_label": "Broadcom official IR Form 10-Q cover page",
            "source_integrity": "official_read_only_unhashed",
            "classes": "single common-stock class; preferred stock authorized but none issued at the balance-sheet date",
            "evidence_note": "10-Q cover count; distinct from the quarter's diluted weighted-average denominator",
        },
        "corporate_action_evidence": [
            {
                "type": "forward_split",
                "ratio": 10.0,
                "effective_date": "2024-07-12",
                "split_adjusted_trading_date": "2024-07-15",
                "source_url": "https://investors.broadcom.com/static-files/5b6d75ad-3f6f-4e5d-a2d6-cf9a517ab9f3",
                "source_label": "Broadcom official Form 8-K",
                "status": "verified_official",
            }
        ],
    },
    "QCOM": {
        "company": "QUALCOMM Incorporated",
        "business": "무선통신 칩셋·특허 라이선스·자동차 및 IoT",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "corporate_action_evidence": [
            {
                "type": "split_history",
                "latest_effective_date": "2004-08-16",
                "latest_ratio": 2.0,
                "source_url": "https://investor.qualcomm.com/about/faq/default.aspx",
                "source_label": "Qualcomm official investor FAQ",
                "status": "verified_official",
            }
        ],
    },
    "GOOGL": {
        "company": "Alphabet Inc.",
        "business": "검색·광고, YouTube, Cloud와 AI 플랫폼",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "share_classes": {
            "listed": ["Class A (GOOGL)", "Class C (GOOG)"],
            "unlisted": ["Class B super-voting"],
            "formal_market_cap_policy": "aggregate all outstanding classes; do not multiply GOOGL price by one-class shares",
        },
        "corporate_action_evidence": [
            {
                "type": "forward_split",
                "ratio": 20.0,
                "effective_date": "2022-07-15",
                "classes_affected": ["Class A", "Class B", "Class C"],
                "source_url": "https://abc.xyz/assets/e1/31/38e9bf5540a2bf9a90221b96aa95/2022-alphabet-annual-report.pdf",
                "source_label": "Alphabet 2022 official annual report",
                "status": "verified_official",
            }
        ],
    },
    "NVDA": {
        "company": "NVIDIA Corporation",
        "business": "AI 가속기, 데이터센터 컴퓨팅과 소프트웨어 플랫폼",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "partial",
        "financial_source_reason": "공식 10-Q의 최근 분기·반기만 확보; FY2026 원문 미연결로 TTM 미산출",
        "corporate_action_evidence": [
            {
                "type": "forward_split",
                "ratio": 10.0,
                "effective_date": "2024-06-07",
                "split_adjusted_trading_date": "2024-06-10",
                "source_url": "https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2025/",
                "source_label": "NVIDIA official investor release",
                "status": "verified_official",
            }
        ],
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
        "corporate_action_evidence": [
            {
                "type": "split_history",
                "latest_effective_date": "2000-08-21",
                "latest_ratio": 2.0,
                "source_url": "https://ir.amd.com/stock-data/stock-split-history",
                "source_label": "AMD official split history",
                "status": "verified_official",
            }
        ],
    },
    "TSM": {
        "company": "Taiwan Semiconductor Manufacturing Company Limited",
        "business": "첨단 반도체 파운드리",
        "accounting_standard": "TIFRS",
        "currency": "TWD",
        "financial_source_state": "blocked",
        "financial_source_reason": "공식 IR PDF가 HTTP 403; TIFRS·TWD·ADR을 US GAAP/USD로 강제 변환하지 않음",
        "adr_evidence": {
            "ads_to_common_share_ratio": 5.0,
            "meaning": "one NYSE-listed ADS represents five TSMC common shares",
            "as_of_document": "2025 annual report",
            "source_url": "https://investor.tsmc.com/sites/ir/annual-report/2025/2025%20Annual%20Report.E.pdf",
            "source_label": "TSMC official 2025 annual report",
            "status": "verified_official",
            "formal_market_cap_policy": "convert common shares to ADS-equivalent or use total equity consistently; never mix TWD common-share counts with USD ADS price",
        },
    },
    "LRCX": {
        "company": "Lam Research Corporation",
        "business": "식각·증착 중심 반도체 제조 장비와 서비스",
        "accounting_standard": "US GAAP",
        "currency": "USD",
        "financial_source_state": "verified",
        "corporate_action_evidence": [
            {
                "type": "forward_split",
                "ratio": 10.0,
                "effective_date": "2024-10-02",
                "split_adjusted_trading_date": "2024-10-03",
                "source_url": "https://investor.lamresearch.com/faqs",
                "source_label": "Lam Research official investor FAQ",
                "status": "verified_official",
            }
        ],
    },
}


def pilot_tickers() -> tuple[str, ...]:
    return tuple(PERSONAL_PILOT_UNIVERSE)
