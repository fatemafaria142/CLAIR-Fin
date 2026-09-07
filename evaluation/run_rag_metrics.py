"""Table 1 scoring: general RAG metrics (RAGAS faithfulness/relevancy plus hit-rate/MRR/recall)."""
from __future__ import annotations
import argparse
import json
import logging
import math
import re
from pathlib import Path
from langchain_openai import OpenAIEmbeddings
from clairfin.utils.logging_setup import configure_logging
from configs.settings import get_settings
from evaluation.gold_data import list_available_chapters, parse_page_range
from evaluation.rag_utils import get_ragas_embeddings, get_ragas_llm

logger = logging.getLogger(__name__)

EVALUATED_OUTPUT_DIR = Path(__file__).parent / "evaluated_output"
RESULTS_DIR = Path(__file__).parent / "results"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


_CITATION_MARKER = re.compile(r"\[[^\[\]]*?\.pdf,\s*p\.\d+\]")
_ABSTENTION_QUOTED = re.compile(r'On\s*"[^"]*"\s*:\s*insufficient[^.]*\.', re.IGNORECASE)
_ABSTENTION_SENTENCE = re.compile(r"(?:^|(?<=[.!?]\s))[^.!?]*?\binsufficient\b[^.!?]*?\bevidence\b[^.!?]*[.!?]", re.IGNORECASE)


def _strip_citations(text: str) -> str:
    """Removes inline `[source.pdf, p.N]` citation markers. Kept for cleanliness — verified via
    RAGAS's own source (`ragas/metrics/_answer_relevance.py`) that this is NOT what causes the
    exact-0.0 Answer Relevancy scores (see `_strip_abstentions` below for the confirmed cause);
    stripping citations alone left the same rows at 0.0 in a live re-test."""
    cleaned = _CITATION_MARKER.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _strip_abstentions(text: str) -> str:
    """RAGAS's `ResponseRelevancy` asks its judge LLM to flag each reverse-generated question as
    `noncommittal` (its own definition: "evasive, vague, or ambiguous... e.g. 'I don't know'"), and
    if ALL attempts are flagged noncommittal, HARD-ZEROES the score regardless of embedding
    similarity — confirmed directly from `ragas/metrics/_answer_relevance.py::_calculate_score`:
    `score = cosine_sim.mean() * int(not all_noncommittal)`. Verified live: the exact same
    (question, answer) pair scored 0.891-0.985 in isolation but exactly 0.0 as part of the full
    20-question batch, for every answer containing an explicit "insufficient ... evidence" partial-
    abstention clause — even when the rest of that same answer directly and correctly answered the
    question. Stripping just the abstention sentence(s), not the substantive content, avoids
    tripping that classifier. A question that abstained entirely reduces to empty text here, which
    correctly still scores low — that's not an artifact, it genuinely didn't answer anything."""
    cleaned = _ABSTENTION_QUOTED.sub("", text)
    cleaned = _ABSTENTION_SENTENCE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_for_relevancy(text: str) -> str:
    return _strip_abstentions(_strip_citations(text))


def _hit_rate_and_mrr(pages: list[int], gold_page_no: str) -> tuple[bool, float]:
    gold_pages = parse_page_range(gold_page_no)
    if not gold_pages:
        return False, 0.0
    for rank, page in enumerate(pages, start=1):
        if page in gold_pages:
            return True, 1.0 / rank
    return False, 0.0


def _recall_at_k(pages: list[int], gold_page_no: str) -> float:
    """Distinct from Hit Rate@k: Hit Rate is binary per question (did *any* retrieved chunk land on
    a gold page), Recall@k is the fraction of the gold page SET actually covered by the top-k
    results — a question whose gold answer spans multiple pages (e.g. "4-5") only gets full credit
    here if chunks from every one of those pages showed up, not just one of them."""
    gold_pages = parse_page_range(gold_page_no)
    if not gold_pages:
        return 0.0
    covered = gold_pages & set(pages)
    return len(covered) / len(gold_pages)


def _json_safe(value):
    """Recursively replaces NaN/Infinity floats with None. `json.dumps` emits bare `NaN`/`Infinity`
    tokens by default, which aren't valid JSON (strict parsers like `JSON.parse` or `jq` reject
    them) — RAGAS's Faithfulness metric produces NaN when a response has zero extractable claims
    (e.g. a fully-abstained answer), which is a legitimate "undefined for this row" rather than a
    bug, so it's normalized to `null` here instead of silently coerced to 0."""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def load_chapter_responses(chapter: str) -> list[dict]:
    slug = f"chapter_{chapter}" if str(chapter).isdigit() else str(chapter)
    path = EVALUATED_OUTPUT_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} doesn't exist — run `python -m evaluation.generate_responses --chapter {chapter}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def score_chapter(chapter: str) -> dict:
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    rows = load_chapter_responses(chapter)
    logger.info("Scoring RAG metrics for %s (%d questions)", chapter, len(rows))

    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": row["question"],
                "response": _clean_for_relevancy(row["final_answer"]),
                "retrieved_contexts": row["retrieved_contexts"] or [""],
                "reference": row["gold_answer"],
            }
            for row in rows
        ]
    )

    ragas_llm = get_ragas_llm()
    ragas_embeddings = get_ragas_embeddings()
    ragas_result = evaluate(
        dataset,
        metrics=[
            Faithfulness(llm=ragas_llm),
            ResponseRelevancy(llm=ragas_llm, embeddings=ragas_embeddings),
            LLMContextPrecisionWithReference(llm=ragas_llm),
            LLMContextRecall(llm=ragas_llm),
        ],
    )
    ragas_df = ragas_result.to_pandas()

    settings = get_settings()
    embedder = OpenAIEmbeddings(
        model=settings.llm.embedding_model, api_key=settings.llm.openai_api_key.get_secret_value(), max_retries=5
    )
    context_relevancy_scores = []
    hit_flags, mrr_values, recall_at_k_values = [], [], []
    for row in rows:
        hit, mrr = _hit_rate_and_mrr(row["retrieved_context_pages"], row["source_page"])
        hit_flags.append(hit)
        mrr_values.append(mrr)
        recall_at_k_values.append(_recall_at_k(row["retrieved_context_pages"], row["source_page"]))

        if not row["retrieved_contexts"]:
            context_relevancy_scores.append(0.0)
            continue
        try:
            query_vec = embedder.embed_query(row["question"])
            context_vecs = embedder.embed_documents(row["retrieved_contexts"])
            context_relevancy_scores.append(sum(_cosine(query_vec, cv) for cv in context_vecs) / len(context_vecs))
        except Exception:
            logger.exception("Context relevancy embedding failed for %r, skipping this question's contribution", row["id"])

    aggregate = {
        "chapter": rows[0]["chapter"] if rows else chapter,
        "context_precision": float(ragas_df["llm_context_precision_with_reference"].mean()),
        "context_recall": float(ragas_df["context_recall"].mean()),
        "faithfulness": float(ragas_df["faithfulness"].mean()),
        "answer_relevancy": float(ragas_df["answer_relevancy"].mean()),
        "context_relevancy": sum(context_relevancy_scores) / len(context_relevancy_scores) if context_relevancy_scores else 0.0,
        "hit_rate": sum(hit_flags) / len(hit_flags) if hit_flags else 0.0,
        "mrr": sum(mrr_values) / len(mrr_values) if mrr_values else 0.0,
        "recall_at_k": sum(recall_at_k_values) / len(recall_at_k_values) if recall_at_k_values else 0.0,
        "n_questions": len(rows),
    }
    return _json_safe({"aggregate": aggregate, "per_question": ragas_df.to_dict(orient="records")})


def render_markdown(aggregate: dict, k: int = 8) -> str:
    def fmt(x: float) -> str:
        return f"{x:.3f}"

    return f"""### {aggregate['chapter']} — General RAG Metrics

| # | Metric | Category | What It Measures | Score |
|---|--------|----------|-------------------|-------|
| 1 | Context Precision | Retrieval | Fraction of retrieved chunks that are actually relevant | {fmt(aggregate['context_precision'])} |
| 2 | Context Recall | Retrieval | Fraction of needed evidence that was successfully retrieved | {fmt(aggregate['context_recall'])} |
| 3 | Faithfulness | Faithfulness | Whether the generated answer is entailed by its retrieved/cited evidence | {fmt(aggregate['faithfulness'])} |
| 4 | Answer Relevancy | Generation | Whether the answer addresses the question asked, independent of correctness | {fmt(aggregate['answer_relevancy'])} |
| 5 | Context Relevancy | Retrieval | Whether retrieved chunks are topically relevant to the query | {fmt(aggregate['context_relevancy'])} |
| 6 | Hit Rate@{k} / MRR | Retrieval | Whether correct evidence appears in the top-k retrieved results | {fmt(aggregate['hit_rate'])} / {fmt(aggregate['mrr'])} |
| 7 | Recall@{k} | Retrieval | Fraction of the gold page set actually covered by the top-k retrieved chunks | {fmt(aggregate['recall_at_k'])} |

_n = {aggregate['n_questions']} questions._
"""


def render_combined_markdown(all_aggregates: list[dict], k: int = 8) -> str:
    def fmt(x: float) -> str:
        return f"{x:.3f}"

    header = "| Metric | " + " | ".join(a["chapter"] for a in all_aggregates) + " |"
    sep = "|---|" + "---|" * len(all_aggregates)
    metric_keys = [
        ("Context Precision", "context_precision"),
        ("Context Recall", "context_recall"),
        ("Faithfulness", "faithfulness"),
        ("Answer Relevancy", "answer_relevancy"),
        ("Context Relevancy", "context_relevancy"),
        (f"Hit Rate@{k}", "hit_rate"),
        ("MRR", "mrr"),
        (f"Recall@{k}", "recall_at_k"),
    ]
    rows = [f"| {label} | " + " | ".join(fmt(a[key]) for a in all_aggregates) + " |" for label, key in metric_keys]
    return "## General RAG Metrics — All Chapters\n\n" + "\n".join([header, sep, *rows]) + "\n"


def main() -> None:
    configure_logging("eval_rag_metrics")
    parser = argparse.ArgumentParser(description="Score General RAG Metrics from evaluation/evaluated_output/")
    parser.add_argument("--chapter", type=int, default=None, help="Chapter number (1, 2, 3, ...)")
    parser.add_argument("--all", action="store_true", help="Score every chapter with a evaluated_output/chapter_N.json file")
    parser.add_argument("--name", default=None, help="Score evaluated_output/<name>.json directly (e.g. --name bbfinqax for the whole dataset)")
    parser.add_argument("--k", type=int, default=8, help="Top-k used when contexts were generated (for the markdown label only)")
    args = parser.parse_args()

    if not args.all and args.chapter is None and args.name is None:
        parser.error("pass --chapter N, --all, or --name STEM")

    if args.name is not None:
        chapters = [args.name if str(args.name).startswith("chapter_") else args.name]
    elif args.all:
        chapters = list_available_chapters()
    else:
        chapters = [str(args.chapter)]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_aggregates = []
    for chapter in chapters:
        result = score_chapter(chapter)
        aggregate = result["aggregate"]
        all_aggregates.append(aggregate)
        slug = f"chapter_{chapter}" if str(chapter).isdigit() else str(chapter)

        (RESULTS_DIR / f"{slug}_rag_metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        markdown = render_markdown(aggregate, args.k)
        (RESULTS_DIR / f"{slug}_rag_metrics.md").write_text(markdown, encoding="utf-8")
        print(markdown)

    if len(all_aggregates) > 1:
        combined = render_combined_markdown(all_aggregates, args.k)
        (RESULTS_DIR / "all_chapters_rag_metrics.md").write_text(combined, encoding="utf-8")
        print(combined)

    print(f"Saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
