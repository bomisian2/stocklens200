from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.evidence_inventory import resolve_repository_private_data  # noqa: E402
from stocklens.personal.nvda_research import (  # noqa: E402
    NvdaResearchError,
    adjudicate_partial_estimates,
    build_consensus_read_model,
    build_provisional_research_decision,
)


CONTINUATION_RUN_ID = (
    "p5031-5ef15a9c0dc999f5555d2aaf6bb4e9eaa8c79044-continuation-approved-01"
)
SUPPLEMENTAL_RUN_ID = "p501-e0f7040-supplemental-approved-01"


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _paths(project_root: Path, top10_data_root: Path) -> dict[str, Path]:
    private_root = resolve_repository_private_data(project_root)
    continuation = private_root / "raw" / "alpha_vantage" / CONTINUATION_RUN_ID
    supplemental = private_root / "raw" / "alpha_vantage" / SUPPLEMENTAL_RUN_ID
    return {
        "private_root": private_root,
        "raw": continuation / "NVDA_EARNINGS_ESTIMATES.response.raw",
        "manifest": continuation / "NVDA_EARNINGS_ESTIMATES.response.raw.manifest.json",
        "ledger": continuation / "continuation-collection-run.json",
        "price_raw": supplemental / "NVDA_TIME_SERIES_DAILY.response.raw",
        "price_manifest": supplemental / "NVDA_TIME_SERIES_DAILY.response.raw.manifest.json",
        "macro": top10_data_root / "macro-snapshot.json",
        "valuation": top10_data_root / "valuation-history.json",
        "config": project_root / "config" / "decision-model-v1.json",
        "adjudication": private_root / "decision" / "nvda-estimates-partial-adjudication-p5.1.json",
        "read_model": private_root / "normalized" / "research" / "NVDA-consensus-p5.1.json",
        "decision": private_root / "decision" / "nvda-provisional-research-decision-p5.1.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build offline NVDA P5.1 partial-valid research artifacts",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--top10-data-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "나스닥 Top10 대시보드" / "data",
    )
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    paths = _paths(project_root, args.top10_data_root)
    safe_plan = {
        "network_calls": 0,
        "immutable_inputs": [
            str(paths[key])
            for key in ("raw", "manifest", "ledger", "price_raw", "price_manifest", "macro", "valuation")
        ],
        "private_outputs": [
            str(paths[key].relative_to(paths["private_root"]))
            for key in ("adjudication", "read_model", "decision")
        ],
        "parser_candidate": False,
        "snapshot_created": False,
        "operational_eligible": False,
    }
    if not args.execute:
        print(json.dumps(safe_plan, ensure_ascii=False, indent=2))
        return 0
    try:
        before = {key: _sha256(paths[key]) for key in ("raw", "manifest", "ledger")}
        adjudication = adjudicate_partial_estimates(
            raw_path=paths["raw"],
            manifest_path=paths["manifest"],
            ledger_path=paths["ledger"],
            output_path=paths["adjudication"],
        )
        adjudication_hash = _sha256(paths["adjudication"])
        read_model = build_consensus_read_model(
            raw_path=paths["raw"],
            adjudication=adjudication,
            adjudication_sha256=adjudication_hash,
            output_path=paths["read_model"],
        )
        read_model_hash = _sha256(paths["read_model"])
        decision = build_provisional_research_decision(
            read_model=read_model,
            read_model_sha256=read_model_hash,
            price_raw_path=paths["price_raw"],
            price_manifest_path=paths["price_manifest"],
            macro_proxy_path=paths["macro"],
            valuation_history_path=paths["valuation"],
            config_path=paths["config"],
            output_path=paths["decision"],
        )
        after = {key: _sha256(paths[key]) for key in ("raw", "manifest", "ledger")}
        if before != after:
            raise NvdaResearchError("immutable evidence changed during P5.1 build")
    except (OSError, ValueError, NvdaResearchError) as exc:
        print(f"P5.1 offline build failed: {exc}", file=sys.stderr)
        return 2
    result = {
        **safe_plan,
        "adjudication_sha256": adjudication_hash,
        "read_model_sha256": read_model_hash,
        "decision_sha256": _sha256(paths["decision"]),
        "row_counts": adjudication["row_counts"],
        "research_action": decision["research_action"],
        "operational_eligible": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
