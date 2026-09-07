"""Two-stage retriever that narrows to top-matching section summaries before ranking chunks, for hierarchical-RAG."""
from __future__ import annotations

from langchain_core.documents import Document

from clairfin.ingestion.ingest import get_vector_store
from clairfin.retrieval.retriever import _LEXICAL_WEIGHT, _OVERFETCH_FACTOR, _lexical_overlap, _tokenize

_TOP_SECTIONS = 2  # how many section summaries' page ranges count as "in-section" for stage 2


def _matching_pages(store, query: str) -> set[int]:
    summary_hits = store.similarity_search_with_score(query, k=_TOP_SECTIONS, expr='metadata["modality"] == "summary"')
    pages: set[int] = set()
    for doc, _distance in summary_hits:
        start, end = doc.metadata.get("page_start"), doc.metadata.get("page_end")
        if start is not None and end is not None:
            pages.update(range(start, end + 1))
    return pages


def retrieve_with_scores(query: str, k: int = 5, modality: str | None = None) -> list[tuple[Document, float]]:
    store = get_vector_store()
    in_section_pages = _matching_pages(store, query)

    modality_expr = f'metadata["modality"] == "{modality}"' if modality else 'metadata["modality"] != "summary"'
    candidate_k = max(k * _OVERFETCH_FACTOR, k)
    results = store.similarity_search_with_score(query, k=candidate_k, expr=modality_expr)
    if not results:
        return []

    query_terms = _tokenize(query)
    blended = [
        (
            doc,
            (1.0 - _LEXICAL_WEIGHT) * (1.0 / (1.0 + distance)) + _LEXICAL_WEIGHT * _lexical_overlap(query_terms, doc.page_content),
        )
        for doc, distance in results
    ]

    in_section = sorted(
        (pair for pair in blended if pair[0].metadata.get("page") in in_section_pages),
        key=lambda pair: pair[1],
        reverse=True,
    )
    out_of_section = sorted(
        (pair for pair in blended if pair[0].metadata.get("page") not in in_section_pages),
        key=lambda pair: pair[1],
        reverse=True,
    )
    # In-section candidates rank first regardless of their raw blended score — that's the whole
    # point of the hierarchical stage — with out-of-section candidates backfilling any remaining
    # slots so a cross-section answer still gets `k` results.
    return (in_section + out_of_section)[:k]


def retrieve(query: str, k: int = 5, modality: str | None = None) -> list[Document]:
    return [doc for doc, _ in retrieve_with_scores(query, k=k, modality=modality)]
