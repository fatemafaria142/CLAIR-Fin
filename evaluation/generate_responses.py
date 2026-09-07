"""Run the CLAIR-Fin pipeline over a chapter's gold questions and save responses + retrieved contexts."""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from clairfin.graph.run import run_question
from clairfin.utils.logging_setup import configure_logging
from evaluation.gold_data import (
    GoldQuestion,
    hf_chapters,
    list_available_chapters,
    load_chapter_questions,
    load_hf_questions,
)
from evaluation.rag_utils import retrieve_contexts

logger = logging.getLogger(__name__)

EVALUATED_OUTPUT_DIR = Path(__file__).parent / "evaluated_output"


def generate_chapter(
    chapter: str,
    *,
    k: int,
    output_suffix: str | None = None,
    questions: list[GoldQuestion] | None = None,
) -> Path:
    """`output_suffix`, if given, is appended to the saved filename (`chapter_1_hyde.json` instead
    of `chapter_1.json`) without changing which gold question set is loaded — used by the
    retrieval-ablation experiments (`ablation-study/retrieval-ablation/*/run.py`) to run the same
    chapter's questions through a swapped-out retriever without overwriting the baseline's saved
    output. `questions`, if given, is used as-is (e.g. loaded from Hugging Face) instead of reading
    a local `evaluation/questions/*.json` file; `chapter` is then only the output filename stem."""
    if questions is None:
        questions = load_chapter_questions(chapter)
    logger.info("Generating responses for %s (%d questions)", questions[0].chapter if questions else chapter, len(questions))

    rows = []
    for q in questions:
        logger.info("[%s] %s", q.id, q.question)
        report = run_question(q.question)
        contexts = retrieve_contexts(q.question, k=k)
        rows.append(
            {
                "id": q.id,
                "number": q.number,
                "chapter": q.chapter,
                "chapter_title": q.chapter_title,
                "question": q.question,
                "gold_answer": q.answer,
                "evidence": q.evidence,
                "query_type": q.query_type,
                "reasoning_skill": q.reasoning_skill,
                "presentation_format": q.presentation_format,
                "difficulty": q.difficulty,
                "source_page": q.source_page,
                "final_answer": report["final_answer"],
                "abstained": report["abstained"],
                "claims": report["claims"],
                "run_id": report.get("run_id"),
                "answer_path": report.get("answer_path"),
                "authority_docket_summary": report.get("authority_docket_summary"),
                "retrieved_contexts": [c.text for c in contexts],
                "retrieved_context_pages": [c.page for c in contexts],
            }
        )

    EVALUATED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = chapter if str(chapter).startswith("chapter_") else f"chapter_{chapter}"
    if output_suffix:
        slug = f"{slug}_{output_suffix}"
    out_path = EVALUATED_OUTPUT_DIR / f"{slug}.json"
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    logger.info("Saved %d responses to %s", len(rows), out_path)
    return out_path


def main() -> None:
    configure_logging("eval_generate")
    parser = argparse.ArgumentParser(description="Run BB-FinQA-X gold questions through the live pipeline")
    parser.add_argument("--chapter", type=int, default=None, help="Chapter number (1-9); omit with --hf to run the whole dataset")
    parser.add_argument("--all", action="store_true", help="Run every chapter, one output file each")
    parser.add_argument("--hf", action="store_true", help="Load gold questions from Hugging Face (Fatema142/BB-FinQA-X) instead of local evaluation/questions/*.json")
    parser.add_argument("--k", type=int, default=8, help="Retrieval top-k to save per question (default 8)")
    args = parser.parse_args()

    if args.hf:
        if args.all:
            for chapter in hf_chapters():
                generate_chapter(f"chapter_{chapter}", k=args.k, questions=load_hf_questions(chapter))
        elif args.chapter is not None:
            generate_chapter(f"chapter_{args.chapter}", k=args.k, questions=load_hf_questions(args.chapter))
        else:
            generate_chapter("bbfinqax", k=args.k, questions=load_hf_questions())
        return

    if not args.all and args.chapter is None:
        parser.error("pass --chapter N, --all, or --hf")

    chapters = list_available_chapters() if args.all else [str(args.chapter)]
    if not chapters:
        parser.error("no evaluation/questions/chapter_*.json files found — run `python -m evaluation.gold_data` or pass --hf")

    for chapter in chapters:
        generate_chapter(chapter, k=args.k)


if __name__ == "__main__":
    main()
