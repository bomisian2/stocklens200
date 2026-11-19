from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.decision_contracts import (  # noqa: E402
    CONTRACT_ROLES,
    DecisionContractError,
    build_candidate_registries,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build candidate registries only; independent approval is a separate step"
    )
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--usage-policy-input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--generated-by", required=True)
    parser.add_argument("--generated-at", required=True)
    for role in CONTRACT_ROLES:
        flag = "--regime-contract" if role == "regime" else f"--{role.replace('_', '-')}"
        parser.add_argument(flag, dest=f"{role}_contract", required=True)
    args = parser.parse_args()
    paths = {role: getattr(args, f"{role}_contract") for role in CONTRACT_ROLES}
    try:
        result = build_candidate_registries(
            paths,
            actual_evidence_root=args.evidence_root,
            usage_policy_input_path=args.usage_policy_input,
            output_dir=args.output_dir,
            generated_by=args.generated_by,
            generated_at=args.generated_at,
        )
    except (OSError, DecisionContractError, ValueError) as exc:
        print(f"same-date candidate generation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
