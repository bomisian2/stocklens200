from __future__ import annotations

import argparse
import json
from pathlib import Path


def _as_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes"}


def validate_provider_rights(
    manifest: dict, *, purpose: str, operator_confirmed: bool = False
) -> None:
    """Block network diagnostics unless both human and manifest gates allow them."""

    if purpose == "manual_diagnostic" and not operator_confirmed:
        raise SystemExit("provider diagnostic blocked: operator confirmation is false")
    if purpose not in {"manual_diagnostic", "scheduled_refresh"}:
        raise SystemExit("provider diagnostic blocked: unsupported purpose")
    if manifest.get("automated_external_collection_authorized") is not True:
        raise SystemExit("provider diagnostic blocked: rights manifest does not authorize external collection")

    providers = manifest.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise SystemExit("provider diagnostic blocked: rights manifest has no providers")
    blocked = sorted(
        name
        for name, policy in providers.items()
        if not isinstance(policy, dict) or policy.get("diagnostic_call_authorized") is not True
    )
    if blocked:
        raise SystemExit(
            "provider diagnostic blocked: provider authorization is false for " + ", ".join(blocked)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the provider diagnostic rights gate")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--purpose", choices=("manual_diagnostic", "scheduled_refresh"), required=True
    )
    parser.add_argument("--operator-confirmed", default="false")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_provider_rights(
        manifest,
        purpose=args.purpose,
        operator_confirmed=_as_bool(args.operator_confirmed),
    )
    print("provider diagnostic rights gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
