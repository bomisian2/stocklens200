from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.consensus_basis_audit import (  # noqa: E402
    audit_nvda_consensus_basis,
    load_consensus_basis_audit,
    write_consensus_basis_audit,
)
from stocklens.personal.evidence_inventory import resolve_repository_private_data  # noqa: E402


NASDAQ_SNAPSHOT = (
    "data/consensus-evidence/2026-10-19/"
    "e06893280257c4522e8b0f46d7144995ee04e2f5fb57e51230061f302111f4aa.json"
)
ALPHA_RUN = (
    "p5031-5ef15a9c0dc999f5555d2aaf6bb4e9eaa8c79044-"
    "continuation-approved-01"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit local NVDA consensus basis without network access"
    )
    parser.add_argument("--top10-root", type=Path, required=True)
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    private = resolve_repository_private_data(repository_root, require_exists=True)
    top10 = args.top10_root.resolve(strict=True)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    alpha_root = private / "raw" / "alpha_vantage" / ALPHA_RUN
    output = private / "decision" / "nvda-consensus-basis-audit-v1.json"

    audit = audit_nvda_consensus_basis(
        nasdaq_snapshot_path=top10 / NASDAQ_SNAPSHOT,
        nasdaq_collector_path=top10 / "scripts" / "refresh-top100-consensus.mjs",
        alpha_raw_path=alpha_root / "NVDA_EARNINGS_ESTIMATES.response.raw",
        alpha_manifest_path=alpha_root / "NVDA_EARNINGS_ESTIMATES.response.raw.manifest.json",
        alpha_adjudication_path=(
            private / "decision" / "nvda-estimates-partial-adjudication-p5.1.json"
        ),
        eps_unit_evidence_path=top10 / "data" / "eps-unit-evidence.json",
        generated_at=generated_at,
    )
    write_consensus_basis_audit(output, audit)
    loaded = load_consensus_basis_audit(output)
    if loaded != audit:
        raise RuntimeError("written consensus basis audit failed round-trip verification")
    print(
        "Consensus basis audit written: "
        f"{output.name}; verified_price_map={loaded['verified_price_map']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
