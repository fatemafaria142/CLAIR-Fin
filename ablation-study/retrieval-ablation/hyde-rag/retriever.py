"""HyDE retriever: search with an LLM-generated hypothetical answer instead of the raw query."""
from __future__ import annotations

from langchain_core.documents import Document

from clairfin.ingestion.ingest import get_vector_store
from clairfin.retrieval.retriever import _LEXICAL_WEIGHT, _OVERFETCH_FACTOR, _lexical_overlap, _tokenize
from clairfin.utils.llm import get_chat_llm

_HYDE_PROMPT = (
    "Write a short, factual-sounding passage (3-5 sentences) of the kind that would appear in a "
    "central bank's macroeconomic annual report, that directly answers the question below. State "
    "specific figures, metric names, and terms even if you are not certain they are correct — this "
    "passage is only used to steer a document search and is never shown to a user.\n\n"
    "Question: {question}"
)


def _generate_hypothetical_document(query: str) -> str:
    llm = get_chat_llm(temperature=0.3, max_tokens=200)
    response = llm.invoke(_HYDE_PROMPT.format(question=query))
    content = response.content
    return content if isinstance(content, str) else str(content)


def retrieve_with_scores(query: str, k: int = 5, modality: str | None = None) -> list[tuple[Document, float]]:
    """Same signature and blending logic as the baseline retriever — only the text handed to
    Milvus's similarity search is different (the hypothetical document, not the raw query).
    Lexical overlap is still scored against the *original* query's terms, so the re-rank guard
    still rewards passages that name the query's actual subject even if the hypothetical document
    drifted from it."""
    store = get_vector_store()
    hypothetical_doc = _generate_hypothetical_document(query)
    expr = f'metadata["modality"] == "{modality}"' if modality else None
    candidate_k = max(k * _OVERFETCH_FACTOR, k)
    results = store.similarity_search_with_score(hypothetical_doc, k=candidate_k, expr=expr)
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
    return [doc for doc, _ in retrieve_with_scores(query, k=k, modality=modality)]
