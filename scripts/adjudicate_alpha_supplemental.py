from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from stocklens.personal.supplemental_alpha_adjudication import (  # noqa: E402
    adjudicate_supplemental_run,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an offline, non-promoting P5.0.2 supplemental adjudication",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    result = adjudicate_supplemental_run(
        project_root=REPOSITORY_ROOT,
        run_id=args.run_id,
        generated_at=args.generated_at,
    )
    print(json.dumps({
        "adjudication_path": str(result["adjudication_path"].relative_to(REPOSITORY_ROOT)),
        "adjudication_sha256": result["adjudication_sha256"],
        "collection_run_sha256": result["collection_run_sha256"],
        "parser_candidate_count": 0,
        "decision_created": False,
        "operational_eligible": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
