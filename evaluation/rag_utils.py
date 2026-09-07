"""Shared helpers for RAGAS-based evaluation: LLM/embeddings wrappers and context retrieval."""
from __future__ import annotations
from dataclasses import dataclass
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from clairfin.retrieval.retriever import retrieve_with_scores
from configs.settings import get_settings


def get_ragas_llm():
    """Lazy import — `ragas` is an eval-only dependency, not part of the core pipeline's
    requirements, so importing it here (not at module load time) keeps `clairfin/` itself free of
    an eval-only dependency."""
    from ragas.llms import LangchainLLMWrapper

    settings = get_settings()
    chat = ChatOpenAI(
        model=settings.llm.chat_model,
        api_key=settings.llm.openai_api_key.get_secret_value(),
        temperature=0,
    )
    return LangchainLLMWrapper(chat)


def get_ragas_embeddings():
    from ragas.embeddings import LangchainEmbeddingsWrapper

    settings = get_settings()
    embeddings = OpenAIEmbeddings(
        model=settings.llm.embedding_model,
        api_key=settings.llm.openai_api_key.get_secret_value(),
    )
    return LangchainEmbeddingsWrapper(embeddings)


@dataclass
class RetrievedContext:
    text: str
    page: int


def retrieve_contexts(question: str, k: int = 8) -> list[RetrievedContext]:
    """Top-k chunks across all modalities for `question` — captured once at generation time and
    saved to `evaluation/evaluated_output/chapter_N.json`; the general RAG metrics' Context Precision/Recall,
    Context Relevancy, and Hit Rate@k/MRR all score against this same saved snapshot."""
    results = retrieve_with_scores(question, k=k, modality=None)
    return [RetrievedContext(text=doc.page_content, page=doc.metadata.get("page", -1)) for doc, _ in results]
