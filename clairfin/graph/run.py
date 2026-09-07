"""CLI/programmatic entry point: runs one question through the compiled CLAIR-Fin graph and persists the ledger + answer report under results/<run_id>/."""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from clairfin.graph.build import build_graph
from clairfin.ingestion.ingest import get_vector_store
from clairfin.ledger.graph import ClaimLedger
from clairfin.schemas.claim import ClaimTask
from clairfin.tools.authority_docket import summarize as summarize_authority_docket
from clairfin.utils.cost_tracker import track_run
from clairfin.utils.logging_setup import configure_logging
from configs.settings import get_settings

logger = logging.getLogger(__name__)


def _build_answer_report(
    run_id: str, question: str, final_answer: str, abstained: bool, claim_tasks: list[ClaimTask], verdicts: list[dict], ledger: ClaimLedger
) -> dict:
    """A flat, self-contained "where did this come from" view of one run — everything needed to
    answer that without traversing the ledger graph by hand. `ledger.json` remains the complete,
    low-level audit trail (every node/edge/custody event/authority decision); this is the
    human-readable summary layered on top of it, one entry per claim.

    Two source lists per claim, deliberately kept separate: `sources_considered` is everything the
    three evidence agents actually gathered for this claim (useful for understanding *why* a claim
    abstained — what was available but didn't clear the bar), `cited_source_ids` is the subset
    that's actually referenced in the published `answer_fragment` (empty when the claim abstained).
    """
    claims_report = []
    for claim, verdict in zip(claim_tasks, verdicts):
        support_ids = ledger.support_subgraph(claim.claim_id)
        claims_report.append(
            {
                "claim_id": claim.claim_id,
                "claim_text": claim.text,
                "claim_type": claim.claim_type,
                "verdict": verdict["label"],
                "audit_passed": verdict["audit_passed"],
                "winning_modality": verdict["winning_modality"],
                "confidence": verdict["confidence"],
                "was_debated": verdict["was_debated"],
                "hallucination_risk_index": verdict["hallucination_risk_index"],
                "answer_fragment": verdict["answer_fragment"],
                "cited_source_ids": verdict["citations"],
                "sources_considered": [ledger.get_node(node_id).model_dump() for node_id in support_ids],
                "custody_events": [event.model_dump() for event in ledger.custody_log_for(claim.claim_id)],
            }
        )

    return {
        "run_id": run_id,
        "question": question,
        "final_answer": final_answer,
        "abstained": abstained,
        "claims": claims_report,
        "authority_docket_summary": summarize_authority_docket(ledger),
    }


def run_question(question: str) -> dict:
    """Run one question through the pipeline end to end: invoke the graph, persist the ledger and
    answer report under `results/<run_id>/`, and return the report (the same dict written to
    `answer.json`). Shared by the CLI (`main`, below) and `server/main.py`'s API endpoint so both
    go through one code path.
    """
    # Construct (and cache) the vector store client here, on the main thread, before the graph's
    # parallel extractor nodes query it from worker threads — client construction isn't
    # guaranteed safe from a non-main thread on first use; doing it once up front is harmless
    # either way and avoids the question entirely.
    get_vector_store()

    graph = build_graph()
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    # Wraps the whole graph invocation so every chat/embedding call made by any agent — including
    # LangGraph's parallel extractor branches, which run on worker threads — attributes its token
    # usage/cost back to this run (clairfin/utils/cost_tracker.py).
    with track_run(run_id) as cost_tracker:
        # LangGraph's default recursion_limit (25) counts node executions across the WHOLE run, not
        # per claim — a multi-claim question (e.g. a 4-country comparison, one claim per country) can
        # exceed 25 total steps even though each claim's own debate loop is correctly bounded by
        # configs/agent_budgets.yaml. 12 claims (the configured max) x ~10 steps/claim in the worst
        # case (extractors + guard + full debate/custody loop + judge + advance) comfortably fits 150.
        result = graph.invoke({"question": question}, config={"recursion_limit": 150})
        token_usage = cost_tracker.summary()

    settings = get_settings()
    settings.paths.ensure_runtime_dirs()
    run_dir = settings.paths.results_dir / run_id

    ledger_path = run_dir / "ledger.json"
    result["ledger"].save(ledger_path)
    logger.info("Ledger saved to %s", ledger_path)

    report = _build_answer_report(
        run_id=run_id,
        question=question,
        final_answer=result["final_answer"],
        abstained=result["abstained"],
        claim_tasks=result["claim_tasks"],
        verdicts=result["verdicts"],
        ledger=result["ledger"],
    )
    report["token_usage"] = token_usage
    answer_path = run_dir / "answer.json"
    answer_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Answer report (with sources) saved to %s", answer_path)
    report["answer_path"] = str(answer_path)
    return report


def main() -> None:
    configure_logging("run")
    if len(sys.argv) < 2:
        print('Usage: python -m clairfin.graph.run "<question>"')
        raise SystemExit(1)
    question = " ".join(sys.argv[1:])

    report = run_question(question)
    result = {
        "final_answer": report["final_answer"],
        "abstained": report["abstained"],
        "verdicts": [
            {
                "claim_id": c["claim_id"],
                "label": c["verdict"],
                "winning_modality": c["winning_modality"],
                "confidence": c["confidence"],
                "was_debated": c["was_debated"],
                "hallucination_risk_index": c["hallucination_risk_index"],
            }
            for c in report["claims"]
        ],
    }
    answer_path = report["answer_path"]

    print("\n=== ANSWER ===")
    print(result["final_answer"])
    print(f"\nAbstained: {result['abstained']}")
    print("\n=== VERDICTS ===")
    for verdict in result["verdicts"]:
        hri = verdict.get("hallucination_risk_index")
        hri_str = f"{hri:.3f}" if hri is not None else "n/a"
        print(
            f"- {verdict['claim_id']}: {verdict['label']} "
            f"(modality={verdict['winning_modality']}, confidence={verdict['confidence']:.2f}, "
            f"debated={verdict['was_debated']}, HRI={hri_str})"
        )

    docket_summary = report["authority_docket_summary"]
    print("\n=== AUTHORITY DOCKET ===")
    print(
        f"AEA changed the outcome vs. equal-weight voting in {docket_summary['changed_outcome']}/"
        f"{docket_summary['decisions']} decisions ({docket_summary['changed_outcome_rate']:.0%})."
    )
    if docket_summary["modalities_favored_by_asymmetry"]:
        print(f"Modalities favored by asymmetry: {docket_summary['modalities_favored_by_asymmetry']}")

    print(f"\nFull sources: {answer_path}")


if __name__ == "__main__":
    main()
