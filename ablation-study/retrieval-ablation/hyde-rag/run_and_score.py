"""Run the HyDE-RAG ablation end-to-end (generate + Table 1/2 scoring) and write a consolidated report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from retriever import retrieve, retrieve_with_scores  # this folder's HyDE retriever  # noqa: E402

METHOD_SUFFIX = "hyde"
METHOD_LABEL = "HyDE RAG"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
OUT_MD = Path(__file__).parent / "RESULTS.md"


def patch_retrieval() -> None:
    import clairfin.agents.narrative_evidence as narrative_evidence
    import clairfin.agents.planner_orchestrator as planner_orchestrator
    import clairfin.agents.tabular_evidence as tabular_evidence
    import clairfin.agents.visual_evidence as visual_evidence
    import evaluation.rag_utils as rag_utils

    narrative_evidence.retrieve_with_scores = retrieve_with_scores
    tabular_evidence.retrieve_with_scores = retrieve_with_scores
    visual_evidence.retrieve_with_scores = retrieve_with_scores
    planner_orchestrator.retrieve = retrieve
    rag_utils.retrieve_with_scores = retrieve_with_scores


def generate(chapters: list[int], k: int) -> None:
    from evaluation.generate_responses import generate_chapter

    patch_retrieval()
    for ch in chapters:
        print(f"=== [{METHOD_LABEL}] generating chapter {ch} ===", flush=True)
        out_path = generate_chapter(ch, k=k, output_suffix=METHOD_SUFFIX)
        print(f"Saved {out_path}", flush=True)


def score(chapters: list[int], k: int) -> tuple[list[dict], list[dict]]:
    from evaluation.run_rag_metrics import render_markdown as render_t1, score_chapter
    from evaluation.run_clairfin_metrics import (
        aggregate_results,
        load_chapter_responses,
        render_markdown as render_t2,
        score_question,
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t1_aggregates, t2_aggregates = [], []

    for ch in chapters:
        slug_chapter = f"{ch}_{METHOD_SUFFIX}"
        slug = f"chapter_{slug_chapter}"

        print(f"=== [{METHOD_LABEL}] Table 1 scoring chapter {ch} ===", flush=True)
        result = score_chapter(slug_chapter)
        t1_aggregate = result["aggregate"]
        t1_aggregates.append(t1_aggregate)
        (RESULTS_DIR / f"{slug}_table1.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (RESULTS_DIR / f"{slug}_table1.md").write_text(render_t1(t1_aggregate, k), encoding="utf-8")

        print(f"=== [{METHOD_LABEL}] Table 2 scoring chapter {ch} ===", flush=True)
        raw_rows = load_chapter_responses(slug_chapter)
        chapter_label = raw_rows[0]["chapter"] if raw_rows else slug_chapter
        scored_rows = []
        for row in raw_rows:
            try:
                scored_rows.append(score_question(row))
            except Exception:  # noqa: BLE001 - one bad question shouldn't kill the whole chapter
                print(f"Question {row.get('id')} failed Table 2 scoring, skipping", flush=True)
        t2_aggregate = aggregate_results(f"{chapter_label} ({METHOD_LABEL})", scored_rows)
        t2_aggregates.append(t2_aggregate)
        (RESULTS_DIR / f"{slug}_table2.json").write_text(
            json.dumps({"aggregate": t2_aggregate, "per_question": scored_rows}, indent=2), encoding="utf-8"
        )
        (RESULTS_DIR / f"{slug}_table2.md").write_text(render_t2(t2_aggregate), encoding="utf-8")

    return t1_aggregates, t2_aggregates


def _mean(aggregates: list[dict], key: str) -> float:
    values = [a[key] for a in aggregates if a.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


def write_consolidated_markdown(chapters: list[int], t1_aggregates: list[dict], t2_aggregates: list[dict], k: int) -> None:
    from evaluation.run_rag_metrics import render_combined_markdown as combined_t1
    from evaluation.run_clairfin_metrics import render_combined_markdown as combined_t2

    t1_overall = {
        "context_precision": _mean(t1_aggregates, "context_precision"),
        "context_recall": _mean(t1_aggregates, "context_recall"),
        "faithfulness": _mean(t1_aggregates, "faithfulness"),
        "answer_relevancy": _mean(t1_aggregates, "answer_relevancy"),
        "context_relevancy": _mean(t1_aggregates, "context_relevancy"),
        "hit_rate": _mean(t1_aggregates, "hit_rate"),
        "mrr": _mean(t1_aggregates, "mrr"),
        "recall_at_k": _mean(t1_aggregates, "recall_at_k"),
        "n_questions": sum(a.get("n_questions", 0) for a in t1_aggregates),
    }
    t2_rate_keys = [
        "coverage",
        "selective_accuracy",
        "faithfulness_rate",
        "citation_precision_proxy",
        "citation_recall_proxy",
        "debate_utilization_rate",
        "debated_audit_pass_rate",
        "fast_path_audit_pass_rate",
        "aea_impact_rate",
    ]
    t2_overall = {key: _mean(t2_aggregates, key) for key in t2_rate_keys} if t2_aggregates else {}

    lines = [
        f"# {METHOD_LABEL} Ablation — Results",
        "",
        f"Chapters run: {', '.join(str(c) for c in chapters)} ({sum(a.get('n_questions', 0) for a in t1_aggregates)} questions total).",
        "Same Milvus collection, same gold question sets, same RAGAS/Table-2 harness as the baseline "
        "pipeline (`evaluation/generate_responses.py`) — only retrieval was swapped for HyDE "
        "(`ablation-study/retrieval-ablation/hyde-rag/retriever.py`).",
        "",
        "## Overall (mean across chapters)",
        "",
        "### Table 1 — General RAG Metrics",
        "",
        "| Metric | Score |",
        "|---|---|",
        f"| Context Precision | {t1_overall['context_precision']:.3f} |",
        f"| Context Recall | {t1_overall['context_recall']:.3f} |",
        f"| Faithfulness | {t1_overall['faithfulness']:.3f} |",
        f"| Answer Relevancy | {t1_overall['answer_relevancy']:.3f} |",
        f"| Context Relevancy | {t1_overall['context_relevancy']:.3f} |",
        f"| Hit Rate@{k} | {t1_overall['hit_rate']:.3f} |",
        f"| MRR | {t1_overall['mrr']:.3f} |",
        f"| Recall@{k} | {t1_overall['recall_at_k']:.3f} |",
        "",
    ]

    if t2_overall:
        lines += ["### Table 2 — CLAIR-Fin-Specific Metrics", "", "| Metric | Score |", "|---|---|"]
        lines += [f"| {key} | {value:.3f} |" for key, value in t2_overall.items()]
        lines += [""]

    lines += ["## Per-Chapter Breakdown", "", "### Table 1", "", combined_t1(t1_aggregates, k), ""]
    if t2_aggregates:
        lines += ["### Table 2", "", combined_t2(t2_aggregates), ""]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_MD}", flush=True)


def main() -> None:
    from clairfin.utils.logging_setup import configure_logging

    configure_logging(f"eval_generate_{METHOD_SUFFIX}")
    parser = argparse.ArgumentParser(description=f"Run + score the {METHOD_LABEL} ablation across chapters")
    parser.add_argument("--chapters", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--k", type=int, default=8)
    args = parser.parse_args()

    generate(args.chapters, args.k)
    t1_aggregates, t2_aggregates = score(args.chapters, args.k)
    write_consolidated_markdown(args.chapters, t1_aggregates, t2_aggregates, args.k)


if __name__ == "__main__":
    main()
