"""Narrative-evidence agent: retrieves text spans for a claim, tags hedging/factuality, and extracts any labeled numeric figures for calculator-based derivation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from clairfin.retrieval.retriever import retrieve_with_scores
from clairfin.schemas.evidence import EvidenceItem, HedgeType
from clairfin.schemas.state import GraphState
from clairfin.tools.metric_derivation import derive_metrics
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

TOP_K = 8


class _SpanTag(BaseModel):
    span_index: int
    hedge_type: HedgeType
    # Populated only when the passage states a labeled figure for a metric at a specific period
    # (e.g. "the policy rate rose to 10.00 percent in the second half of FY25") — lets a claim
    # whose two comparable numbers both live in prose get the same deterministic calculator
    # treatment as a table cell (`clairfin.tools.metric_derivation`), instead of leaving the
    # subtraction to the drafting LLM to eyeball. Left unset for passages with no such figure.
    metric_label: str | None = None
    period_label: str | None = None
    value: str | None = None
    unit: str | None = None


class _HedgeTagging(BaseModel):
    tags: list[_SpanTag] = Field(default_factory=list)


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    results = retrieve_with_scores(claim.text, k=TOP_K, modality="text")
    if not results:
        return {"text_evidence": []}

    spans_block = "\n\n".join(f"[SPAN {i}]\n{doc.page_content}" for i, (doc, _score) in enumerate(results))
    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0, max_tokens=budgets["extraction"]["narrative"]["max_output_tokens"]).with_structured_output(
        _HedgeTagging
    )
    tagging = invoke_structured(
        llm,
        [
            ("system", load_prompt("agents/narrative_evidence")),
            ("human", f"CLAIM: {claim.text}\n\n{spans_block}"),
        ],
        default=_HedgeTagging(tags=[]),
    )
    assert isinstance(tagging, _HedgeTagging)
    tags_by_index_full = {tag.span_index: tag for tag in tagging.tags}
    tags_by_index = {i: tag.hedge_type for i, tag in tags_by_index_full.items()}

    evidence = [
        EvidenceItem(
            modality="text",
            source=doc.metadata["source"],
            page=doc.metadata["page"],
            content=doc.page_content,
            confidence=score,
            hedge_type=tags_by_index.get(i, "fact"),
        )
        for i, (doc, score) in enumerate(results)
    ]

    numeric_cells = [
        EvidenceItem(
            modality="text",
            source=doc.metadata["source"],
            page=doc.metadata["page"],
            content=doc.page_content,
            confidence=score,
            row_label=tag.metric_label,
            col_label=tag.period_label,
            value=tag.value,
            unit=tag.unit,
        )
        for i, (doc, score) in enumerate(results)
        if (tag := tags_by_index_full.get(i)) is not None and tag.metric_label and tag.value
    ]
    return {"text_evidence": evidence + derive_metrics(numeric_cells)}
