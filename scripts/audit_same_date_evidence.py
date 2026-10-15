from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.decision_evidence import (  # noqa: E402
    DecisionEvidenceError,
    audit_same_date_evidence,
    write_audit_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local evidence before creating a same-date decision")
    parser.add_argument("--legacy-snapshot", required=True)
    parser.add_argument("--decision-ledger", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--regime", required=True)
    parser.add_argument("--price-history", required=True)
    parser.add_argument("--decision-snapshot")
    parser.add_argument("--v23-ledger")
    parser.add_argument("--decision-config")
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = audit_same_date_evidence(
            legacy_snapshot_path=args.legacy_snapshot,
            decision_ledger_path=args.decision_ledger,
            consensus_path=args.consensus,
            regime_path=args.regime,
            price_history_path=args.price_history,
            decision_time=args.decision_time,
            decision_snapshot_path=args.decision_snapshot,
            v23_ledger_path=args.v23_ledger,
            decision_config_path=args.decision_config,
        )
        output = write_audit_report(report, args.output)
    except (OSError, DecisionEvidenceError) as exc:
        print(f"same-date evidence audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output), "status": report["status"], "decision_creation_allowed": report["decision_creation_allowed"], "block_stage": report["block_stage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
