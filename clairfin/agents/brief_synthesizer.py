"""Brief-synthesizer agent: composes the final cited answer from audited per-claim verdicts, falling back to raw statements if composition drops a citation."""
from __future__ import annotations

from clairfin.schemas.state import GraphState
from clairfin.utils.llm import get_chat_llm
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings


def _compose(question: str, raw_lines: list[str], citation_labels: list[str]) -> str | None:
    if not raw_lines:
        return None
    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0.2, max_tokens=budgets["synthesizer"]["max_output_tokens"])
    response = llm.invoke(
        [
            ("system", load_prompt("agents/brief_synthesizer")),
            (
                "human",
                f"QUESTION: {question}\n\nAUDITED STATEMENTS (compose from these only):\n"
                + "\n".join(f"- {line}" for line in raw_lines),
            ),
        ]
    )
    composed = str(response.content).strip()
    if citation_labels and not all(label in composed for label in citation_labels):
        return None  # a citation was dropped or altered — don't risk it, fall back to raw
    return composed


def run(state: GraphState) -> dict:
    ledger = state["ledger"]
    claim_tasks = state["claim_tasks"]
    verdicts = state["verdicts"]

    raw_lines: list[str] = []
    citation_labels: list[str] = []
    citations: list[str] = []
    any_supported = False

    for claim, verdict in zip(claim_tasks, verdicts):
        if verdict["audit_passed"]:
            any_supported = True
            # A claim's citations are per evidence NODE, and several nodes (e.g. two retrieved
            # spans, a table cell, a derived metric) commonly share the same (source, page) —
            # deduping here keeps the answer's citation trail readable ("[doc, p.8]" once) instead
            # of repeating the same page marker 5-10 times, while `verdict["citations"]` itself
            # (used by answer.json's full provenance trail) stays untouched.
            cite_labels = list(
                dict.fromkeys(
                    f"[{ledger.get_node(cid).source}, p.{ledger.get_node(cid).page}]"
                    for cid in verdict["citations"]
                    if hasattr(ledger.get_node(cid), "source")
                )
            )
            citation_labels.extend(cite_labels)
            raw_lines.append(f"{verdict['answer_fragment']} {' '.join(cite_labels)}".strip())
            citations.extend(verdict["citations"])
        else:
            raw_lines.append(f'On "{claim.text}": insufficient grounded evidence to answer confidently — abstaining.')

    raw_answer = "\n".join(raw_lines)
    final_answer = _compose(state["question"], raw_lines, citation_labels) or raw_answer

    return {"final_answer": final_answer, "citations": citations, "abstained": not any_supported}
