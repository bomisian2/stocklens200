from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.decision_model_v2 import (
    build_proxy_decision,
    build_proxy_decision_from_bundle,
    load_decision,
    preserve_existing_artifacts,
    write_decision_and_ledger,
)
from stocklens.personal.decision_input_bundle import (
    create_input_bundle,
    select_latest_consensus_evidence,
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
    selected_consensus, _entry, _manifest_path, _manifest_sha = select_latest_consensus_evidence(
        data_root=root,
        decision_time=decision_time,
    )
    # Validate the mutable originals before copying any bytes into a bundle.
    # This object is deliberately discarded; the persisted decision is built
    # only after the immutable bundle has been promoted.
    build_proxy_decision(
        price_history_path=root / "price-history.json",
        consensus_path=selected_consensus,
        valuation_history_path=root / "valuation-history.json",
        industry_map_path=root / "industry-map.json",
        macro_path=root / "macro-snapshot.json",
        config_path=repository_root / "config" / "decision-model-v2.json",
        decision_time=decision_time,
        code_commit_sha=code_commit_sha,
    )
    bundle_dir, _manifest, manifest_sha = create_input_bundle(
        data_root=root,
        config_path=repository_root / "config" / "decision-model-v2.json",
        decision_root=private / "decision",
        decision_time=decision_time,
        code_commit_sha=code_commit_sha,
    )
    decision = build_proxy_decision_from_bundle(
        bundle_dir=bundle_dir,
        manifest_sha256=manifest_sha,
        code_commit_sha=code_commit_sha,
    )
    decision_path = private / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = private / "decision" / "decision-model-v2.1-signal-ledger.json"
    audit_path = None
    if decision_path.is_file():
        existing_text = decision_path.read_text(encoding="utf-8")
        if '"input_bundle"' not in existing_text:
            audit_path = preserve_existing_artifacts(
                decision_path=decision_path,
                ledger_path=ledger_path,
                reason="schema migration — immutable decision input bundle v1",
            )
    output, ledger = write_decision_and_ledger(
        decision=decision,
        decision_path=decision_path,
        ledger_path=ledger_path,
    )
    loaded = load_decision(output)
    if loaded != decision:
        raise RuntimeError("written Decision Model v2.1 artifact failed round-trip verification")
    print(
        f"Decision Model v2.1 written: {output.name}; ledger: {ledger.name}; "
        f"bundle: {bundle_dir.name}; manifest_sha256: {manifest_sha}"
    )
    if audit_path is not None:
        print(f"Previous artifacts preserved: {audit_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
