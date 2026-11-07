from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocklens.personal.evidence_inventory import resolve_repository_private_data  # noqa: E402
from stocklens.personal.nvda_research_p511 import (  # noqa: E402
    NvdaResearchP511Error,
    build_p511_artifacts,
)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _paths(project_root: Path, top10_data_root: Path) -> dict[str, Path]:
    private_root = resolve_repository_private_data(project_root)
    return {
        "private_root": private_root,
        "p51_adjudication": private_root / "decision" / "nvda-estimates-partial-adjudication-p5.1.json",
        "p51_read_model": private_root / "normalized" / "research" / "NVDA-consensus-p5.1.json",
        "p51_decision": private_root / "decision" / "nvda-provisional-research-decision-p5.1.json",
        "terms_review": private_root / "decision" / "alpha-vantage-terms-review-p4.3.1-p422-8c6d282-approved-01.json",
        "supplemental_adjudication": private_root / "decision" / "alpha-vantage-supplemental-adjudication-p5.0.2-p501-e0f7040-supplemental-approved-01.json",
        "price_history": top10_data_root / "price-history.json",
        "consensus": top10_data_root / "top100-consensus.json",
        "valuation_history": top10_data_root / "valuation-history.json",
        "industry_map": top10_data_root / "industry-map.json",
        "macro": top10_data_root / "macro-snapshot.json",
        "supersession": private_root / "decision" / "nvda-p511-supersession-p5.1.2.json",
        "peer_proxy": private_root / "normalized" / "research" / "NVDA-peer-proxy-p5.1.2.json",
        "decision": private_root / "decision" / "nvda-research-state-p5.1.2.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build offline NVDA P5.1.2 gated research artifacts")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--top10-data-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "나스닥 Top10 대시보드" / "data",
    )
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    paths = _paths(project_root, args.top10_data_root)
    immutable_keys = (
        "p51_adjudication",
        "p51_read_model",
        "p51_decision",
        "terms_review",
        "supplemental_adjudication",
        "price_history",
        "consensus",
        "valuation_history",
        "industry_map",
        "macro",
    )
    plan = {
        "network_calls": 0,
        "immutable_inputs": [str(paths[key]) for key in immutable_keys],
        "private_outputs": [
            str(paths[key].relative_to(paths["private_root"]))
            for key in ("supersession", "peer_proxy", "decision")
        ],
        "alpha_vantage_usage": "internal_integrity_and_row_quality_audit_only",
        "operational_eligible": False,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    try:
        before = {key: _sha256(paths[key]) for key in immutable_keys}
        supersession, peer, decision = build_p511_artifacts(
            private_root=paths["private_root"],
            p51_adjudication_path=paths["p51_adjudication"],
            p51_read_model_path=paths["p51_read_model"],
            p51_decision_path=paths["p51_decision"],
            terms_review_path=paths["terms_review"],
            supplemental_adjudication_path=paths["supplemental_adjudication"],
            price_history_path=paths["price_history"],
            consensus_path=paths["consensus"],
            valuation_history_path=paths["valuation_history"],
            industry_map_path=paths["industry_map"],
            macro_path=paths["macro"],
            supersession_output_path=paths["supersession"],
            peer_output_path=paths["peer_proxy"],
            decision_output_path=paths["decision"],
        )
        after = {key: _sha256(paths[key]) for key in immutable_keys}
        if before != after:
            raise NvdaResearchP511Error("immutable evidence changed during P5.1.2 build")
    except (OSError, ValueError, NvdaResearchP511Error) as exc:
        print(f"P5.1.2 offline build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        **plan,
        "supersession_sha256": _sha256(paths["supersession"]),
        "peer_proxy_sha256": _sha256(paths["peer_proxy"]),
        "decision_sha256": _sha256(paths["decision"]),
        "supersession_status": supersession["status"],
        "peer_gate_passed": peer["peer_gate_passed"],
        "accepted_peers": [row["ticker"] for row in peer["post_outlier_peers"]],
        "research_action": decision["research_action"],
        "valuation_signal_status": decision["valuation_signal_status"],
        "control_action": decision["control_action"],
        "independent_target_multiple": decision["independent_target_multiple"],
        "independent_research_base_value": decision["independent_research_base_value"],
        "buy_ladder_status": decision["buy_ladder_status"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
