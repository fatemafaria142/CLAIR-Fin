"""Visual-evidence agent: retrieves chart/figure passages for a claim and extracts grounded findings from them via a vision-capable LLM."""
from __future__ import annotations

from pydantic import BaseModel, Field

from clairfin.retrieval.retriever import retrieve_with_scores
from clairfin.schemas.evidence import EvidenceItem
from clairfin.schemas.state import GraphState
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

TOP_K = 5


class _ChartFinding(BaseModel):
    chart_index: int
    finding: str


class _ChartGrounding(BaseModel):
    findings: list[_ChartFinding] = Field(default_factory=list)


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    results = retrieve_with_scores(claim.text, k=TOP_K, modality="chart")
    if not results:
        return {"chart_evidence": []}

    charts_block = "\n\n".join(
        f"[CHART {i}] (page {doc.metadata['page']}, type: {doc.metadata.get('chart_type')}, "
        f"caption: {doc.metadata.get('caption') or 'none'})\n{doc.page_content}"
        for i, (doc, _score) in enumerate(results)
    )

    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0, max_tokens=budgets["extraction"]["visual"]["max_output_tokens"]).with_structured_output(
        _ChartGrounding
    )
    grounding = invoke_structured(
        llm,
        [
            ("system", load_prompt("agents/visual_evidence")),
            ("human", f"CLAIM: {claim.text}\n\n{charts_block}"),
        ],
        default=_ChartGrounding(findings=[]),
    )
    assert isinstance(grounding, _ChartGrounding)

    evidence: list[EvidenceItem] = []
    for finding in grounding.findings:
        if not (0 <= finding.chart_index < len(results)):
            continue
        doc, score = results[finding.chart_index]
        evidence.append(
            EvidenceItem(
                modality="chart",
                source=doc.metadata["source"],
                page=doc.metadata["page"],
                content=finding.finding,
                confidence=score,
            )
        )
    return {"chart_evidence": evidence}
