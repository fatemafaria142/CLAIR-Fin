"""Adversarial-counsel agent: attacks the affirmative brief's claim/evidence pairing to surface numeric, scope, temporal, and citation weaknesses before publication."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from clairfin.schemas.state import GraphState
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

AttackCategory = Literal[
    "numeric", "scope", "fy_temporal", "causal_overclaim", "citation_gap", "visual_over_precision"
]


class _Attack(BaseModel):
    category: AttackCategory
    detail: str
    severity: Literal["low", "medium", "high"]


class _Scorecard(BaseModel):
    attacks: list[_Attack] = Field(default_factory=list)
    recommend_abstain: bool


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    ledger = state["ledger"]
    support_ids = ledger.support_subgraph(claim.claim_id)
    evidence_text = "\n".join(ledger.describe(node_id) for node_id in support_ids) or "(no evidence found)"
    brief = state.get("affirmative_brief") or "(no brief produced)"

    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0.2, max_tokens=budgets["debate"]["adversarial"]["max_output_tokens"]).with_structured_output(
        _Scorecard
    )
    # A truncated critique degrades to "no findings, don't preemptively abstain" rather than the
    # more conservative-looking "recommend_abstain=True" — the citation-entailment gate downstream
    # (clairfin/tools/entailment.py) is the actual fail-closed check on the published answer; this
    # agent's job is to raise objections, not to be the last line of defense on its own.
    scorecard = invoke_structured(
        llm,
        [
            ("system", load_prompt("agents/adversarial_counsel")),
            ("human", f"CLAIM: {claim.text}\n\nEVIDENCE:\n{evidence_text}\n\nAFFIRMATIVE BRIEF:\n{brief}"),
        ],
        default=_Scorecard(attacks=[], recommend_abstain=False),
    )
    assert isinstance(scorecard, _Scorecard)
    return {
        "adversarial_findings": [attack.model_dump() for attack in scorecard.attacks],
        "recommend_abstain": scorecard.recommend_abstain,
    }
