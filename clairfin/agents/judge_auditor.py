"""Judge-auditor agent: orders evidence by AEA authority, drafts and entailment-checks the claim verdict, and scores its hallucination risk index."""
from __future__ import annotations

from clairfin.schemas.aea import AuthorityDecision
from clairfin.schemas.ledger import ChartRegion, DerivedMetric, TableCell, TextSpan
from clairfin.schemas.state import GraphState
from clairfin.tools.aea_scorer import load_aea_weights, score_claim, score_claim_uniform
from clairfin.tools.entailment import check_entailment
from clairfin.tools.hallucination_risk import compute_hri
from clairfin.utils.llm import get_chat_llm
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

_NODE_TYPE_TO_MODALITY = {
    TextSpan: "text",
    TableCell: "table",
    ChartRegion: "chart",
    DerivedMetric: "tool_derived",
}


def _order_by_authority(ledger, support_ids: list[str], claim_type: str) -> list[str]:
    """AEA governs which modality's evidence *wins* (`score_claim`) and how much a claim's
    coverage is trusted (`coverage_score`) — but until now it never touched what the Judge's
    drafting LLM actually saw: `evidence_text` was every modality's account in retrieval order,
    with no signal about which to prefer. When sources conflicted (verified case: a table cell
    reading 6.27% versus a chart reading "approximately 7 percent" for the same metric), the
    draft prompt's only instinct was "contradictory evidence -> abstain," which threw away a
    conflict AEA was specifically designed to resolve. Ordering evidence by authority weight, and
    telling the draft prompt about the ordering, is what actually lets AEA resolve it instead of
    just labeling the outcome after the fact."""
    weights = load_aea_weights().weights_for(claim_type)

    def weight_of(node_id: str) -> float:
        modality = _NODE_TYPE_TO_MODALITY.get(type(ledger.get_node(node_id)), "")
        return weights.get(modality, 0.0)

    return sorted(support_ids, key=weight_of, reverse=True)


def _empty_verdict(
    claim_id: str, winning_modality: str | None = None, confidence: float = 0.0, was_debated: bool = False
) -> dict:
    return {
        "claim_id": claim_id,
        "label": "InsufficientEvidence",
        "winning_modality": winning_modality,
        "confidence": confidence,
        "citations": [],
        "audit_passed": False,
        "answer_fragment": "",
        "was_debated": was_debated,
        "hallucination_risk_index": None,
    }


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    ledger = state["ledger"]
    support_ids = ledger.support_subgraph(claim.claim_id)
    was_debated = bool(state.get("escalate"))

    if state.get("custody_broken"):
        return {"verdicts": state["verdicts"] + [_empty_verdict(claim.claim_id, was_debated=was_debated)]}

    if not support_ids:
        return {"verdicts": state["verdicts"] + [_empty_verdict(claim.claim_id, was_debated=was_debated)]}

    modality_confidences: dict[str, float] = {}
    for node_id in support_ids:
        node = ledger.get_node(node_id)
        modality = _NODE_TYPE_TO_MODALITY.get(type(node))
        if modality is None:
            continue
        edge_score = ledger.edge_score(node_id, claim.claim_id)
        modality_confidences[modality] = max(modality_confidences.get(modality, 0.0), edge_score)

    winning_modality, authority_score = score_claim(claim.claim_type, modality_confidences)

    # Authority Docket: log this decision against its equal-weight counterfactual regardless of
    # what happens next, so AEA's influence is visible even on claims that end up abstaining.
    uniform_winner, _ = score_claim_uniform(claim.claim_type, modality_confidences)
    ledger.log_authority_decision(
        AuthorityDecision(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            modality_confidences=modality_confidences,
            asymmetric_winner=winning_modality,
            uniform_winner=uniform_winner,
        )
    )

    if state.get("recommend_abstain"):
        return {
            "verdicts": state["verdicts"]
            + [_empty_verdict(claim.claim_id, winning_modality, authority_score, was_debated)]
        }

    ordered_ids = _order_by_authority(ledger, support_ids, claim.claim_type)
    evidence_text = "\n".join(ledger.describe(node_id) for node_id in ordered_ids)
    draft_llm = get_chat_llm(temperature=0, max_tokens=load_yaml(get_settings().agent_budgets_path)["judge_auditor"]["max_output_tokens"])
    draft_prompt = load_prompt("agents/judge_auditor")
    draft = str(
        draft_llm.invoke([("system", draft_prompt), ("human", f"CLAIM: {claim.text}\n\nEVIDENCE:\n{evidence_text}")]).content
    ).strip()

    if draft.startswith("INSUFFICIENT EVIDENCE"):
        return {
            "verdicts": state["verdicts"]
            + [_empty_verdict(claim.claim_id, winning_modality, authority_score, was_debated)]
        }

    entailment = check_entailment(premise=evidence_text, hypothesis=draft)
    threshold = load_yaml(get_settings().agent_budgets_path)["judge_auditor"]["entailment_pass_threshold"]
    audit_passed = entailment.label == "entails" and entailment.confidence >= threshold

    if not audit_passed:
        return {
            "verdicts": state["verdicts"]
            + [_empty_verdict(claim.claim_id, winning_modality, authority_score, was_debated)]
        }

    custody_config = load_yaml(get_settings().agent_budgets_path)["custody"]
    custody_repairs = sum(1 for e in ledger.custody_log_for(claim.claim_id) if e.verdict == "repaired")
    high_severity_attacks = sum(1 for f in (state.get("adversarial_findings") or []) if f["severity"] == "high")
    hri = compute_hri(
        entailment_confidence=entailment.confidence,
        authority_score=authority_score,
        custody_repairs=custody_repairs,
        max_repairs=max(custody_config["max_repair_attempts"], 1),
        high_severity_attacks=high_severity_attacks,
    )

    label = "StronglySupported" if authority_score >= 0.5 else "Supported"
    verdict = {
        "claim_id": claim.claim_id,
        "label": label,
        "winning_modality": winning_modality,
        "confidence": authority_score,
        "citations": support_ids,
        "audit_passed": True,
        "answer_fragment": draft,
        "was_debated": was_debated,
        "hallucination_risk_index": hri,
    }
    return {"verdicts": state["verdicts"] + [verdict]}
