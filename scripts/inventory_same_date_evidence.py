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
        description="Inventory preserved Same-Date evidence without network access or promotion"
    )
    parser.add_argument("--private-data-root", required=True)
    parser.add_argument("--top10-data-root", required=True)
    parser.add_argument("--inspected-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = build_p4_evidence_inventory(
            private_data_root=args.private_data_root,
            top10_data_root=args.top10_data_root,
            inspected_at=args.inspected_at,
        )
        output = write_p4_inventory(report, args.output)
    except (OSError, EvidenceInventoryError) as exc:
        print(f"same-date evidence inventory failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(output),
                "inventory_sha256": hashlib.sha256(output.read_bytes()).hexdigest().upper(),
                "status": report["status"],
                "block_stage": report["block_stage"],
                "provider_native_raw_count": report["provider_native_raw_count"],
                "provider_parser_eligible_count": report["provider_parser_eligible_count"],
                "network_calls": report["network_calls"],
                "decision_created": report["decision_created"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
