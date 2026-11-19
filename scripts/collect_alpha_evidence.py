from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.offline_alpha_collector import (  # noqa: E402
    MAX_CALLS,
    OfflineCollectorError,
    api_key_from_environment,
    collection_report_is_complete,
    collect_approved_alpha_evidence,
    exact_request_plan,
)
from stocklens.personal.evidence_inventory import EvidenceInventoryError  # noqa: E402


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Preserve approved Alpha Vantage evidence locally")
    parser.add_argument("--show-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--confirm-personal-noncommercial", action="store_true")
    args = parser.parse_args()
    if args.show_plan and not args.execute:
        print(json.dumps({
            "logical_operations": exact_request_plan(),
            "maximum_actual_http_requests": MAX_CALLS,
            "automatic_retries": 0,
            "execution_approved": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.execute:
        parser.error("use --show-plan for a zero-call preview or --execute for an approved run")
    key = api_key_from_environment()
    if not key:
        print("Alpha Vantage evidence collection blocked: ALPHAVANTAGE_API_KEY is missing", file=sys.stderr)
        return 2
    try:
        report = collect_approved_alpha_evidence(
            project_root=REPOSITORY_ROOT,
            api_key=key,
            run_id=args.run_id or "",
            personal_noncommercial_confirmed=args.confirm_personal_noncommercial,
            max_calls=args.max_calls,
        )
    except (OfflineCollectorError, EvidenceInventoryError, OSError) as exc:
        print(f"Alpha Vantage evidence collection blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "run_id": report["run_id"],
        "calls_attempted": report["calls_attempted"],
        "calls_completed": report["calls_completed"],
        "actual_http_request_budget": report["approved_actual_http_request_budget"],
        "automatic_retries": report["automatic_retries"],
        "run_state": report["run_state"],
        "failure_code": report["failure_code"],
        "parser_attempted": report["parser_attempted"],
        "decision_created": report["decision_created"],
    }, ensure_ascii=False))
    if not collection_report_is_complete(report):
        print("Alpha Vantage evidence collection did not complete successfully", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
