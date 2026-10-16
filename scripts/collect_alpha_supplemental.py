from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.evidence_inventory import EvidenceInventoryError  # noqa: E402
from stocklens.personal.supplemental_alpha_collector import (  # noqa: E402
    MAX_HTTP_REQUESTS,
    REQUEST_INTERVAL_SECONDS,
    OfflineCollectorError,
    api_key_from_environment,
    collect_alpha_supplemental_evidence,
    exact_supplemental_request_plan,
    supplemental_report_is_complete,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve only the separately approved Alpha Vantage supplemental evidence",
    )
    parser.add_argument("--show-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-reference")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--request-interval-seconds", type=int, default=0)
    parser.add_argument("--confirm-personal-noncommercial", action="store_true")
    parser.add_argument("--confirm-current-provider-limit-reviewed", action="store_true")
    args = parser.parse_args()

    if args.show_plan and not args.execute:
        print(json.dumps({
            "logical_operations": exact_supplemental_request_plan(),
            "maximum_actual_http_requests": MAX_HTTP_REQUESTS,
            "request_interval_seconds": REQUEST_INTERVAL_SECONDS,
            "concurrency": 1,
            "automatic_retries": 0,
            "stop_on_provider_restriction_envelope": True,
            "terms_request_included": False,
            "avgo_daily_request_included": False,
            "execution_approved": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.execute:
        parser.error("use --show-plan for a zero-call preview or --execute after a new approval")

    key = api_key_from_environment()
    if not key:
        print("Supplemental collection blocked: ALPHAVANTAGE_API_KEY is missing", file=sys.stderr)
        return 2
    try:
        report = collect_alpha_supplemental_evidence(
            project_root=REPOSITORY_ROOT,
            api_key=key,
            run_id=args.run_id or "",
            approval_reference=args.approval_reference or "",
            personal_noncommercial_confirmed=args.confirm_personal_noncommercial,
            current_provider_limit_review_confirmed=(
                args.confirm_current_provider_limit_reviewed
            ),
            max_calls=args.max_calls,
            request_interval_seconds=args.request_interval_seconds,
        )
    except (OfflineCollectorError, EvidenceInventoryError, OSError) as exc:
        print(f"Supplemental collection blocked: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "run_id": report["run_id"],
        "calls_attempted": report["calls_attempted"],
        "calls_completed": report["calls_completed"],
        "actual_http_request_budget": report["approved_actual_http_request_budget"],
        "request_interval_seconds": report["request_interval_seconds"],
        "automatic_retries": report["automatic_retries"],
        "run_state": report["run_state"],
        "failure_code": report["failure_code"],
        "parser_attempted": report["parser_attempted"],
        "decision_created": report["decision_created"],
    }, ensure_ascii=False))
    if not supplemental_report_is_complete(report):
        print("Supplemental collection did not complete successfully", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
