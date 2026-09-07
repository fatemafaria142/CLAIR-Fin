"""CLI entry point that monkeypatches the pipeline's retrieval with hierarchical-RAG and generates chapter responses."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from retriever import retrieve, retrieve_with_scores  # this folder's Hierarchical retriever  # noqa: E402

METHOD_SUFFIX = "hierarchical"


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


def _assert_summaries_built() -> None:
    from clairfin.ingestion.ingest import get_vector_store

    store = get_vector_store()
    hits = store.client.query(
        collection_name=store.collection_name,
        filter='metadata["modality"] == "summary"',
        output_fields=["pk"],
        limit=1,
    )
    if not hits:
        raise RuntimeError(
            "No section summaries found in Milvus — run "
            "`python ablation-study/retrieval-ablation/hierarchical-rag/build_summaries.py` first"
        )


def main() -> None:
    from clairfin.utils.logging_setup import configure_logging
    from evaluation.generate_responses import generate_chapter

    configure_logging(f"eval_generate_{METHOD_SUFFIX}")
    parser = argparse.ArgumentParser(description="Generate Hierarchical-RAG responses for a chapter")
    parser.add_argument("--chapter", required=True, help="Chapter number whose gold questions to run, e.g. 1")
    parser.add_argument("--k", type=int, default=8, help="Retrieval top-k to save per question (default 8)")
    args = parser.parse_args()

    _assert_summaries_built()
    patch_retrieval()
    out_path = generate_chapter(args.chapter, k=args.k, output_suffix=METHOD_SUFFIX)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
