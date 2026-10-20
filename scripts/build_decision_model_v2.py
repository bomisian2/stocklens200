from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.decision_model_v2 import (
    build_proxy_decision,
    load_decision,
    preserve_existing_artifacts,
    write_decision_and_ledger,
)


def _git_head(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build offline Decision Model v2.1 proxy artifact")
    parser.add_argument("--top10-root", type=Path, required=True)
    parser.add_argument("--private-data-root", type=Path, required=True)
    parser.add_argument("--decision-time")
    args = parser.parse_args()
    root = args.top10_root.resolve()
    private = args.private_data_root.resolve()
    repository_root = Path(__file__).resolve().parents[1]
    code_commit_sha = _git_head(repository_root)
    decision_time = args.decision_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    decision = build_proxy_decision(
        price_history_path=root / "price-history.json",
        consensus_path=root / "top100-consensus.json",
        valuation_history_path=root / "valuation-history.json",
        industry_map_path=root / "industry-map.json",
        macro_path=root / "macro-snapshot.json",
        config_path=repository_root / "config" / "decision-model-v2.json",
        decision_time=decision_time,
        code_commit_sha=code_commit_sha,
    )
    decision_path = private / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = private / "decision" / "decision-model-v2.1-signal-ledger.json"
    audit_path = preserve_existing_artifacts(
        decision_path=decision_path,
        ledger_path=ledger_path,
        reason="schema drift — peer group meets_minimum field missing",
    )
    output, ledger = write_decision_and_ledger(
        decision=decision,
        decision_path=decision_path,
        ledger_path=ledger_path,
        replace_ledger=True,
    )
    loaded = load_decision(output)
    if loaded != decision:
        raise RuntimeError("written Decision Model v2.1 artifact failed round-trip verification")
    print(f"Decision Model v2.1 written: {output.name}; ledger: {ledger.name}")
    if audit_path is not None:
        print(f"Previous artifacts preserved: {audit_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
