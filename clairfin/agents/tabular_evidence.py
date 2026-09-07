"""Tabular-evidence agent: retrieves and grounds table cells for a claim, dropping low-confidence extractions and deriving calculator-based metrics from grounded cells."""
from __future__ import annotations

from pydantic import BaseModel, Field

from clairfin.retrieval.retriever import retrieve_with_scores
from clairfin.schemas.evidence import EvidenceItem
from clairfin.schemas.state import GraphState
from clairfin.tools.metric_derivation import derive_metrics
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings

TOP_K = 6


class _GroundedCell(BaseModel):
    table_index: int
    row_label: str
    col_label: str
    value: str
    unit: str | None = None


class _TableGrounding(BaseModel):
    cells: list[_GroundedCell] = Field(default_factory=list)


def run(state: GraphState) -> dict:
    claim = state["claim_tasks"][state["claim_index"]]
    results = retrieve_with_scores(claim.text, k=TOP_K, modality="table")
    if not results:
        return {"table_evidence": []}

    tables_block = "\n\n".join(
        f"[TABLE {i}] (page {doc.metadata['page']}, caption: {doc.metadata.get('caption') or 'none'})\n{doc.page_content}"
        for i, (doc, _score) in enumerate(results)
    )

    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0, max_tokens=budgets["extraction"]["tabular"]["max_output_tokens"]).with_structured_output(
        _TableGrounding
    )
    grounding = invoke_structured(
        llm,
        [
            ("system", load_prompt("agents/tabular_evidence")),
            ("human", f"CLAIM: {claim.text}\n\n{tables_block}"),
        ],
        default=_TableGrounding(cells=[]),
    )
    assert isinstance(grounding, _TableGrounding)

    grounded_cells: list[EvidenceItem] = []
    for cell in grounding.cells:
        if not (0 <= cell.table_index < len(results)):
            continue
        doc, score = results[cell.table_index]
        unit_suffix = f" {cell.unit}" if cell.unit else ""

        # `extraction_confidence` is set at ingest time when a table's vision extraction failed to
        # reach agreement across repeated independent attempts, OR was caught by a specific known
        # systematic-misread pattern (`clairfin/ingestion/tables.py::has_duplicated_section_total`)
        # (`clairfin/ingestion/ingest.py`). Excluded outright, not just downweighted: the
        # citation-entailment gate later only checks whether a drafted sentence matches its cited
        # evidence, not whether that evidence is itself correct — a low-confidence cell that's the
        # SOLE evidence for a claim would still sail through entailment and publish confidently,
        # since "faithful to the (wrong) evidence" and "correct" are different things. Confirmed
        # live: a downweighted-but-included low-confidence cell still got published. Dropping it
        # here instead lets the claim correctly escalate/abstain through the normal low-coverage
        # path when no other modality has the answer, rather than publish a number that's flagged
        # as likely wrong.
        if doc.metadata.get("extraction_confidence") == "low":
            continue

        grounded_cells.append(
            EvidenceItem(
                modality="table",
                source=doc.metadata["source"],
                page=doc.metadata["page"],
                content=f"{cell.row_label} / {cell.col_label} = {cell.value}{unit_suffix}",
                confidence=score,
                row_label=cell.row_label,
                col_label=cell.col_label,
                value=cell.value,
                unit=cell.unit,
            )
        )

    return {"table_evidence": grounded_cells + derive_metrics(grounded_cells)}
