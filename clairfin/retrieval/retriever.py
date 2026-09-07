"""Modality-filtered retrieval over the Milvus store, blending vector similarity with lexical term overlap to rerank candidates."""
from __future__ import annotations

import re

from langchain_core.documents import Document

from clairfin.ingestion.ingest import get_vector_store

_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "was", "were", "are",
    "what", "how", "which", "did", "does", "do", "by", "with", "as", "from", "that", "this",
}

_OVERFETCH_FACTOR = 4
_LEXICAL_WEIGHT = 0.35


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _lexical_overlap(query_terms: set[str], content: str) -> float:
    """Fraction of the query's distinctive terms actually present in the candidate passage —
    a cheap keyword guard on top of embedding similarity. Two passages can sit almost equally
    close to a query vector while only one of them actually contains the specific metric name the
    query asked about (confirmed live: "overall balance of payments" and "current account balance"
    embed close enough together that pure vector search let the wrong one win); this catches that
    by rewarding the passage that literally contains the query's key terms."""
    if not query_terms:
        return 0.0
    content_terms = _tokenize(content)
    return len(query_terms & content_terms) / len(query_terms)


def retrieve_with_scores(query: str, k: int = 5, modality: str | None = None) -> list[tuple[Document, float]]:
    """Top-k (document, confidence) pairs, optionally filtered to one modality
    (`text` | `table` | `chart`). Confidence blends two signals: vector similarity
    (`1 / (1 + distance)` — Milvus's default index metric is L2, so lower distance = higher
    similarity, a monotonic ranking heuristic, not a calibrated probability) and lexical term
    overlap with the query, re-ranked over a wider candidate pool than `k` so a vector-nearest but
    lexically-wrong passage doesn't outrank the one that actually names the query's metric.

    All extra metadata (`source`/`page`/`modality`/...) lives inside one JSON field named
    `metadata` (`clairfin/ingestion/ingest.py::get_vector_store`), so filtering uses Milvus's JSON
    path syntax (`metadata["modality"] == "..."`), not a top-level field name.
    """
    store = get_vector_store()
    expr = f'metadata["modality"] == "{modality}"' if modality else None
    candidate_k = max(k * _OVERFETCH_FACTOR, k)
    results = store.similarity_search_with_score(query, k=candidate_k, expr=expr)
    if not results:
        return []

    query_terms = _tokenize(query)
    scored = [
        (doc, 1.0 / (1.0 + distance), _lexical_overlap(query_terms, doc.page_content))
        for doc, distance in results
    ]
    blended = [
        (doc, (1.0 - _LEXICAL_WEIGHT) * vector_score + _LEXICAL_WEIGHT * lexical_score)
        for doc, vector_score, lexical_score in scored
    ]
    blended.sort(key=lambda pair: pair[1], reverse=True)
    return blended[:k]


def retrieve(query: str, k: int = 5, modality: str | None = None) -> list[Document]:
    """Top-k chunks most relevant to `query`, each carrying `source`/`page` metadata for citation."""
    return [doc for doc, _ in retrieve_with_scores(query, k=k, modality=modality)]
