"""Retriever that blends vector/lexical scores with entity-graph connectedness for graph-RAG."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import networkx as nx
from langchain_core.documents import Document

from clairfin.ingestion.ingest import get_vector_store
from clairfin.retrieval.retriever import _LEXICAL_WEIGHT, _OVERFETCH_FACTOR, _lexical_overlap, _tokenize

GRAPH_PATH = Path(__file__).parent / "graph.json"
_GRAPH_WEIGHT = 0.3  # how much graph-connectedness can boost a candidate's blended vector+lexical score


@lru_cache
def _load_graph() -> nx.Graph:
    if not GRAPH_PATH.exists():
        raise RuntimeError(f"{GRAPH_PATH} not found — run `python ablation-study/retrieval-ablation/graph-rag/build_graph.py` first")
    data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    return nx.node_link_graph(data, edges="edges")


def _matched_entities(graph: nx.Graph, query: str) -> list[str]:
    query_lower = query.lower()
    return [node for node, attrs in graph.nodes(data=True) if attrs.get("kind") == "entity" and node in query_lower]


def _graph_scores(graph: nx.Graph, entities: list[str]) -> dict[str, float]:
    """chunk_pk -> fraction of matched entities that chunk is linked to."""
    if not entities:
        return {}
    scores: dict[str, float] = {}
    for entity in entities:
        if entity not in graph:
            continue
        for neighbor in graph.neighbors(entity):
            if graph.nodes[neighbor].get("kind") == "chunk":
                scores[neighbor] = scores.get(neighbor, 0.0) + 1.0 / len(entities)
    return scores


def _fetch_chunk(store, pk: str) -> Document | None:
    rows = store.client.get(collection_name=store.collection_name, ids=[pk], output_fields=["text", "metadata"])
    if not rows:
        return None
    row = rows[0]
    return Document(page_content=str(row["text"]), metadata=row["metadata"])


def retrieve_with_scores(query: str, k: int = 5, modality: str | None = None) -> list[tuple[Document, float]]:
    store = get_vector_store()
    graph = _load_graph()
    entities = _matched_entities(graph, query)
    graph_scores = _graph_scores(graph, entities)

    expr = f'metadata["modality"] == "{modality}"' if modality else 'metadata["modality"] != "summary"'
    candidate_k = max(k * _OVERFETCH_FACTOR, k)
    results = store.similarity_search_with_score(query, k=candidate_k, expr=expr)

    query_terms = _tokenize(query)
    candidates: dict[str, tuple[Document, float]] = {}
    for doc, distance in results:
        # `similarity_search_with_score` only returns the JSON `metadata` field, not the row's own
        # `pk` — but ingestion's `pk` is deterministic from these same fields
        # (`clairfin/ingestion/ingest.py::ingest`), so it's reconstructible without an extra lookup.
        pk = f"{doc.metadata.get('source', '').rsplit('.', 1)[0]}-p{doc.metadata.get('page')}-{doc.metadata.get('modality')}-c{doc.metadata.get('chunk')}"
        base_score = (1.0 - _LEXICAL_WEIGHT) * (1.0 / (1.0 + distance)) + _LEXICAL_WEIGHT * _lexical_overlap(query_terms, doc.page_content)
        candidates[pk] = (doc, base_score)

    # Pull in graph-connected chunks the vector search didn't surface at all, so a chunk that's
    # topically linked but embeds far from the query can still compete.
    for pk in graph_scores:
        if pk not in candidates and (modality is None or modality != "summary"):
            fetched = _fetch_chunk(store, pk)
            if fetched is not None and (modality is None or fetched.metadata.get("modality") == modality):
                candidates[pk] = (fetched, 0.0)

    blended = [
        (doc, (1.0 - _GRAPH_WEIGHT) * base_score + _GRAPH_WEIGHT * graph_scores.get(pk, 0.0))
        for pk, (doc, base_score) in candidates.items()
    ]
    blended.sort(key=lambda pair: pair[1], reverse=True)
    return blended[:k]


def retrieve(query: str, k: int = 5, modality: str | None = None) -> list[Document]:
    return [doc for doc, _ in retrieve_with_scores(query, k=k, modality=modality)]
