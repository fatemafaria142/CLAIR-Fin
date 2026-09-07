"""Affirmative-counsel agent: drafts and revises the evidence-backed brief supporting a claim, responding to custody repairs and adversarial objections."""
from __future__ import annotations

from clairfin.schemas.state import GraphState
from clairfin.utils.llm import get_chat_llm
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings


def _addendum(state: GraphState) -> tuple[str, bool]:
    """Extra instruction for a repair/rebuttal call, and whether this counts as a rebuttal round
    (custody repairs don't consume the debate-round budget; rebuttals do — see `graph/build.py`)."""
    repair_note = state.get("custody_repair_note")
    if repair_note:
        return f"\n\nIMPORTANT — fix this before anything else:\n{repair_note}", False

    findings = state.get("adversarial_findings") or []
    if findings:
        findings_text = "\n".join(f"- [{f['severity']}] {f['category']}: {f['detail']}" for f in findings)
        return (
            "\n\nThe Adversarial Counsel raised these objections to your previous brief. Revise it "
            f"to address them — withdraw or narrow any claim you can no longer support:\n{findings_text}",
            True,
        )
    return "", False


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    ledger = state["ledger"]
    support_ids = ledger.support_subgraph(claim.claim_id)
    evidence_text = "\n".join(ledger.describe(node_id) for node_id in support_ids) or "(no evidence found)"
    addendum, is_rebuttal = _addendum(state)

    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0.2, max_tokens=budgets["debate"]["affirmative"]["max_output_tokens"])
    response = llm.invoke(
        [
            ("system", load_prompt("agents/affirmative_counsel")),
            ("human", f"CLAIM: {claim.text}\n\nEVIDENCE:\n{evidence_text}{addendum}"),
        ]
    )

    update: dict = {"affirmative_brief": str(response.content), "custody_repair_note": None}
    if is_rebuttal:
        update["debate_round"] = state.get("debate_round", 0) + 1
    return update
