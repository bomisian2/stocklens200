from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.collection_adjudication import adjudicate_alpha_run  # noqa: E402
from stocklens.personal.evidence_inventory import EvidenceInventoryError  # noqa: E402


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Adjudicate one preserved Alpha Vantage run offline")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    try:
        result = adjudicate_alpha_run(
            project_root=REPOSITORY_ROOT,
            run_id=args.run_id,
            generated_at=args.generated_at,
        )
    except (OSError, EvidenceInventoryError) as exc:
        print(f"offline Alpha Vantage adjudication failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "adjudication_relative_path": result["adjudication_path"].relative_to(REPOSITORY_ROOT).as_posix(),
        "adjudication_sha256": result["adjudication_sha256"],
        "terms_review_relative_path": result["terms_review_path"].relative_to(REPOSITORY_ROOT).as_posix(),
        "terms_review_sha256": result["terms_review_sha256"],
        "collection_run_sha256": result["collection_run_sha256"],
        "network_calls": 0,
        "parser_candidates": 0,
        "decisions_created": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
