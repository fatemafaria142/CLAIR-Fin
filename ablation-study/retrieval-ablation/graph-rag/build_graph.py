"""Build a page/entity knowledge graph from ingested chunks for the graph-RAG ablation."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import networkx as nx
from pydantic import BaseModel, Field

from clairfin.ingestion.ingest import get_vector_store
from clairfin.utils.llm import get_chat_llm
from clairfin.utils.logging_setup import configure_logging

logger = logging.getLogger(__name__)

GRAPH_PATH = Path(__file__).parent / "graph.json"
MAX_PAGE_CHARS = 6000

_ENTITY_PROMPT = (
    "List the distinct financial/economic entities discussed in the excerpt below: named metrics "
    "(e.g. \"GDP growth\", \"current account balance\", \"inflation rate\"), regions/groups (e.g. "
    "\"advanced economies\", \"emerging markets\"), and fiscal periods (e.g. \"FY24\", \"Q2\"). "
    "Return short noun-phrases exactly as they could appear in text, one concept per phrase, no "
    "duplicates, at most 15 phrases.\n\nExcerpt:\n{excerpt}"
)


class _PageEntities(BaseModel):
    entities: list[str] = Field(default_factory=list)


def _all_source_rows(store) -> list[dict]:
    return store.client.query(
        collection_name=store.collection_name,
        filter='metadata["modality"] != "summary"',
        output_fields=["pk", "text", "metadata"],
        limit=16384,
    )


def build_graph() -> nx.Graph:
    store = get_vector_store()
    rows = _all_source_rows(store)
    if not rows:
        raise RuntimeError("No chunks found in Milvus — run `python -m clairfin.ingestion.ingest` first")

    pages = sorted({r["metadata"]["page"] for r in rows if r["metadata"].get("page") is not None})
    llm = get_chat_llm(temperature=0).with_structured_output(_PageEntities)

    graph = nx.Graph()
    for page in pages:
        page_rows = [r for r in rows if r["metadata"].get("page") == page]
        excerpt = "\n\n".join(str(r["text"]) for r in page_rows)[:MAX_PAGE_CHARS]
        try:
            result = llm.invoke(_ENTITY_PROMPT.format(excerpt=excerpt))
            entities = [e.strip() for e in result.entities if e.strip()]
        except Exception:
            logger.warning("Entity extraction failed for page %d — skipping", page, exc_info=True)
            entities = []

        for entity in entities:
            entity_node = entity.lower()
            graph.add_node(entity_node, kind="entity", label=entity)

        for row in page_rows:
            chunk_pk = row["pk"]
            graph.add_node(chunk_pk, kind="chunk", page=page, source=row["metadata"].get("source"))
            chunk_text_lower = str(row["text"]).lower()
            for entity in entities:
                if entity.lower() in chunk_text_lower:
                    graph.add_edge(chunk_pk, entity.lower())

        logger.info("Page %d: %d entities, %d chunks", page, len(entities), len(page_rows))

    data = nx.node_link_data(graph, edges="edges")
    GRAPH_PATH.write_text(json.dumps(data), encoding="utf-8")
    logger.info("Wrote graph (%d nodes, %d edges) to %s", graph.number_of_nodes(), graph.number_of_edges(), GRAPH_PATH)
    return graph


if __name__ == "__main__":
    configure_logging("build_graph_rag")
    g = build_graph()
    print(f"Built graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges -> {GRAPH_PATH}")
