"""Custody-verifier agent: checks the affirmative brief is grounded in its cited evidence, triggering bounded repair attempts or flagging broken custody."""
from __future__ import annotations

from clairfin.schemas.state import GraphState
from clairfin.tools.custody import CustodyEvent, max_repair_attempts, verify_grounding


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    ledger = state["ledger"]
    support_ids = ledger.support_subgraph(claim.claim_id)
    evidence_text = "\n".join(ledger.describe(node_id) for node_id in support_ids) or "(no evidence found)"
    brief = state.get("affirmative_brief") or ""

    passed, detail = verify_grounding(evidence_text, brief)
    repair_attempts = state.get("custody_repair_attempts", 0)

    if passed:
        ledger.log_custody(
            CustodyEvent(claim_id=claim.claim_id, stage="affirmative_brief", verdict="valid", detail=detail)
        )
        return {"custody_broken": False}

    if repair_attempts < max_repair_attempts():
        ledger.log_custody(
            CustodyEvent(claim_id=claim.claim_id, stage="affirmative_brief", verdict="repaired", detail=detail)
        )
        return {
            "custody_broken": False,
            "custody_repair_attempts": repair_attempts + 1,
            "custody_repair_note": (
                f"Your previous brief was not fully supported by the evidence ({detail}). "
                "Revise it to state only what the evidence actually supports — narrow or drop "
                "anything it doesn't."
            ),
        }

    ledger.log_custody(
        CustodyEvent(claim_id=claim.claim_id, stage="affirmative_brief", verdict="broken", detail=detail)
    )
    return {"custody_broken": True}
