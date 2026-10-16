from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.evidence_inventory import (  # noqa: E402
    EvidenceInventoryError,
    build_p4_evidence_inventory,
    write_p4_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory local Same-Date evidence without network access or promotion"
    )
    parser.add_argument("--private-data-root", required=True)
    parser.add_argument("--top10-data-root", required=True)
    parser.add_argument("--inspected-at", required=True)
    args = parser.parse_args()
    try:
        report = build_p4_evidence_inventory(
            private_data_root=args.private_data_root,
            top10_data_root=args.top10_data_root,
            inspected_at=args.inspected_at,
        )
        output = write_p4_inventory(report, private_data_root=args.private_data_root)
    except (OSError, EvidenceInventoryError) as exc:
        print(f"same-date evidence inventory failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(output),
        "inventory_sha256": hashlib.sha256(output.read_bytes()).hexdigest().upper(),
        "status": report["status"],
        "inventory_complete": report["inventory_complete"],
        "expected_artifact_count": report["expected_artifact_count"],
        "inspected_artifact_count": report["inspected_artifact_count"],
        "missing_artifact_count": report["missing_artifact_count"],
        "unclassified_artifact_count": report["unclassified_artifact_count"],
        "network_calls_observed_in_this_run": report["network_calls_observed_in_this_run"],
        "decisions_created_in_this_run": report["decisions_created_in_this_run"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
