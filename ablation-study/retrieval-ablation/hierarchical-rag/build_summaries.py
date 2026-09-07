"""Generate and upsert page-window section summaries used by the hierarchical-RAG ablation."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import logging

from langchain_core.documents import Document

from clairfin.ingestion.ingest import get_vector_store
from clairfin.utils.llm import get_chat_llm
from clairfin.utils.logging_setup import configure_logging

logger = logging.getLogger(__name__)

# 5-page windows over this 24-page corpus give 5 summary tiers — coarse enough to actually narrow
# the candidate pool, fine enough that a window isn't just "the whole document" again.
WINDOW_PAGES = 5
MAX_WINDOW_CHARS = 8000  # keeps the summarization prompt well under the chat model's context limit

_SUMMARY_PROMPT = (
    "Summarize the following excerpt from a macroeconomic annual report in 4-6 sentences. Name the "
    "specific topics, metrics, and figures discussed (e.g. GDP growth, inflation, balance of "
    "payments, specific percentages/years) so the summary can be matched against topic-specific "
    "questions later.\n\nExcerpt:\n{excerpt}"
)


def _all_source_rows(store) -> list[dict]:
    """Every non-summary row already in the collection, with page + text content."""
    return store.client.query(
        collection_name=store.collection_name,
        filter='metadata["modality"] != "summary"',
        output_fields=["pk", "text", "metadata"],
        limit=16384,
    )


def _page_windows(pages: list[int], window_pages: int) -> list[tuple[int, int]]:
    if not pages:
        return []
    start_page, end_page = min(pages), max(pages)
    windows = []
    start = start_page
    while start <= end_page:
        end = min(start + window_pages - 1, end_page)
        windows.append((start, end))
        start = end + 1
    return windows


def build_summaries() -> int:
    store = get_vector_store()
    rows = _all_source_rows(store)
    if not rows:
        raise RuntimeError("No chunks found in Milvus — run `python -m clairfin.ingestion.ingest` first")

    pages = sorted({row["metadata"]["page"] for row in rows if row["metadata"].get("page") is not None})
    windows = _page_windows(pages, WINDOW_PAGES)
    llm = get_chat_llm(temperature=0, max_tokens=300)

    summary_docs, summary_ids = [], []
    for start, end in windows:
        window_rows = [r for r in rows if start <= r["metadata"].get("page", -1) <= end]
        if not window_rows:
            continue
        excerpt = "\n\n".join(str(r["text"]) for r in window_rows)[:MAX_WINDOW_CHARS]
        source = window_rows[0]["metadata"].get("source", "unknown")
        summary = llm.invoke(_SUMMARY_PROMPT.format(excerpt=excerpt)).content
        summary = summary if isinstance(summary, str) else str(summary)

        summary_docs.append(
            Document(
                page_content=summary,
                metadata={
                    "modality": "summary",
                    "source": source,
                    "page": start,  # keeps the existing `metadata["page"]` field meaningful for this row
                    "page_start": start,
                    "page_end": end,
                },
            )
        )
        summary_ids.append(f"summary-{source}-p{start}-{end}")
        logger.info("Summarized pages %d-%d (%d chars input)", start, end, len(excerpt))

    if store.col is None:
        store.add_documents(summary_docs, ids=summary_ids)
    else:
        store.upsert(ids=summary_ids, documents=summary_docs)
    logger.info("Upserted %d section summaries", len(summary_docs))
    return len(summary_docs)


if __name__ == "__main__":
    configure_logging("build_hierarchical_summaries")
    count = build_summaries()
    print(f"Built {count} section summaries")
