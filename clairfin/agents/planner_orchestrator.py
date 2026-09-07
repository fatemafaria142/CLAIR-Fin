"""Planner-orchestrator agent: decomposes the user question into typed claims, seeding the claim ledger and per-claim graph state for the rest of the pipeline."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from clairfin.ledger.graph import ClaimLedger
from clairfin.retrieval.retriever import retrieve
from clairfin.schemas.claim import ClaimTask, ClaimType
from clairfin.schemas.ledger import Claim
from clairfin.schemas.state import GraphState
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

PREVIEW_TOP_K = 12
PREVIEW_CHARS_PER_ITEM = 300

# Previously hardcoded to max_length=4 — too narrow for enumeration-style questions ("what
# combination of policy actions...", "list the three largest sub-sectors...") that legitimately
# decompose into more than 4 independently-checkable claims. `max_claims_per_question` already
# existed in agent_budgets.yaml but was dead config, never actually read anywhere.
_MAX_CLAIMS = load_yaml(get_settings().agent_budgets_path)["orchestrator"]["max_claims_per_question"]


class _DecomposedClaim(BaseModel):
    text: str
    claim_type: ClaimType


class _DecompositionResult(BaseModel):
    claims: list[_DecomposedClaim] = Field(min_length=1, max_length=_MAX_CLAIMS)


def _retrieval_preview(question: str) -> str:
    """Broad, unfiltered (all modalities) retrieval against the question — context for wording
    claims accurately, not the evidence-gathering pass itself."""
    docs = retrieve(question, k=PREVIEW_TOP_K)
    if not docs:
        return "(no relevant source excerpts found)"
    return "\n".join(
        f"- [{doc.metadata.get('source')}, p.{doc.metadata.get('page')}, {doc.metadata.get('modality', 'text')}] "
        f"{doc.page_content[:PREVIEW_CHARS_PER_ITEM]}"
        for doc in docs
    )


def run(state: GraphState) -> dict:
    question = state["question"]
    preview = _retrieval_preview(question)
    human_message = (
        f"QUESTION: {question}\n\n"
        "RELEVANT SOURCE EXCERPTS (for wording and disambiguation only — this is a preview, not "
        "the actual evidence-gathering pass; don't treat it as exhaustive or as something you may "
        "cite):\n" + preview
    )
    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0, max_tokens=budgets["orchestrator"]["max_output_tokens"]).with_structured_output(
        _DecompositionResult
    )
    # Decomposition can't safely fall back to zero claims (the schema requires >=1, and an empty
    # claim list would mean no evidence gathering happens at all) — instead fall back to one claim
    # that restates the raw question, typed as the broadest evidence-driven claim type, so the
    # normal per-claim pipeline still runs and abstains through the usual coverage/entailment gates
    # rather than crashing the whole run on a single truncated decomposition call.
    result = invoke_structured(
        llm,
        [("system", load_prompt("agents/planner_orchestrator")), ("human", human_message)],
        default=_DecompositionResult(claims=[_DecomposedClaim(text=question, claim_type="CAUSE_ATTRIBUTION")]),
    )
    assert isinstance(result, _DecompositionResult)

    ledger = ClaimLedger()
    claim_tasks: list[ClaimTask] = []
    for decomposed in result.claims:
        claim_id = f"claim-{uuid.uuid4().hex[:8]}"
        ledger.add_node(
            Claim(node_id=claim_id, question=question, claim_type=decomposed.claim_type, text=decomposed.text)
        )
        claim_tasks.append(ClaimTask(claim_id=claim_id, text=decomposed.text, claim_type=decomposed.claim_type))

    return {
        "ledger": ledger,
        "claim_tasks": claim_tasks,
        "claim_index": 0,
        "verdicts": [],
        "debate_round": 0,
        "custody_broken": False,
        "custody_repair_attempts": 0,
        "custody_repair_note": None,
    }
