from __future__ import annotations

import logging
from typing import Any, TypeVar

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from clairfin.utils.cost_tracker import RunCostLoggingCallback, log_embedding_usage
from configs.settings import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def get_chat_llm(temperature: float | None = None, max_tokens: int | None = None, model: str | None = None) -> ChatOpenAI:
    """`max_tokens` should come from `configs/agent_budgets.yaml` (each agent/tool's own
    `max_output_tokens`), not be left unset — an uncapped call has no ceiling below the model's
    absolute max (16384 for the configured chat model), and a runaway generation there fails with
    an unrecoverable `LengthFinishReasonError` instead of a bounded, predictable output.

    `model` defaults to `settings.llm.chat_model` — pass e.g. `settings.llm.vision_model` for the
    stronger model ingestion's page-image reads need. Every call site goes through here (rather
    than constructing `ChatOpenAI` directly) so `RunCostLoggingCallback` — token/cost logging,
    `clairfin/utils/cost_tracker.py` — is attached exactly once, in one place.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm.chat_model if model is None else model,
        api_key=settings.llm.openai_api_key.get_secret_value(),
        temperature=settings.llm.temperature if temperature is None else temperature,
        max_tokens=max_tokens,
        callbacks=[RunCostLoggingCallback()],
    )


class _LoggingOpenAIEmbeddings(OpenAIEmbeddings):
    """`embed_documents`/`embed_query` wrapped so every embedding call logs its token count and
    estimated cost (`log_embedding_usage`) — embeddings don't fire LangChain callbacks the way chat
    completions do, so this is done via subclassing rather than a callback."""

    def embed_documents(self, texts: list[str], chunk_size: int | None = None) -> list[list[float]]:
        log_embedding_usage(self.model, texts)
        return super().embed_documents(texts, chunk_size=chunk_size)

    def embed_query(self, text: str) -> list[float]:
        log_embedding_usage(self.model, [text])
        return super().embed_query(text)


def get_embeddings() -> OpenAIEmbeddings:
    """Single construction point for the embedding client, mirroring `get_chat_llm` above."""
    settings = get_settings()
    return _LoggingOpenAIEmbeddings(
        model=settings.llm.embedding_model,
        api_key=settings.llm.openai_api_key.get_secret_value(),
    )


def invoke_structured(structured_llm: Any, messages: list, default: T) -> T:
    """Call a `.with_structured_output(...)`-wrapped chat model and return `default` instead of
    raising when the model's output gets truncated (hits `max_tokens`) before it can close valid
    structured JSON — a real failure mode seen live even with a token cap set, when the model gets
    stuck in a repetitive generation. Every call site here passes a `default` that degrades the
    pipeline safely (e.g. empty evidence, so the claim escalates/abstains through the existing
    coverage machinery) rather than one that could look like a confident result — callers on the
    entailment gate specifically must NOT default to a passing verdict."""
    try:
        return structured_llm.invoke(messages)
    except Exception as exc:
        if type(exc).__name__ in {"LengthFinishReasonError", "OutputParserException"}:
            logger.warning("Structured LLM call truncated/unparseable (%s) — using safe default", exc)
            return default
        raise
