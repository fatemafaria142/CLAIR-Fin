from __future__ import annotations

import logging
import threading
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import tiktoken
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from configs.loaders import load_yaml
from configs.settings import get_settings

logger = logging.getLogger(__name__)

_FALLBACK_ENCODING = "cl100k_base"


def _load_pricing() -> dict[str, dict[str, float]]:
    return load_yaml(get_settings().pricing_path)


def _pricing_rates(model: str) -> dict[str, float] | None:
    """OpenAI's API responses report a dated snapshot (`gpt-4o-mini-2024-07-18`), not the bare
    alias `pricing.yaml` is keyed by (`gpt-4o-mini`, matching `configs/settings.py`'s
    `chat_model`/`vision_model` values) — confirmed live: every call in a 20-question chapter run
    logged `cost_usd=unknown` despite `gpt-4o-mini` having a pricing entry, because `dict.get`
    requires an exact match. Longest-matching-prefix lookup handles both forms without needing a
    duplicate entry per dated snapshot."""
    pricing = _load_pricing()
    if model in pricing:
        return pricing[model]
    matches = [key for key in pricing if model.startswith(f"{key}-")]
    if not matches:
        return None
    return pricing[max(matches, key=len)]


def _cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """`None` (not 0.0) when a model has no pricing entry — lets call sites log "cost unknown"
    instead of silently under-reporting spend for a model nobody's added a rate for yet."""
    rates = _pricing_rates(model)
    if rates is None:
        return None
    return (input_tokens * rates.get("input", 0.0) + output_tokens * rates.get("output", 0.0)) / 1_000_000


class CostTracker:
    """Thread-safe accumulator for one run: totals overall and broken down per model."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._per_model: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        self._unpriced_models: set[str] = set()

    def record(self, model: str, input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        with self._lock:
            entry = self._per_model[model]
            entry["calls"] += 1
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            if cost_usd is None:
                self._unpriced_models.add(model)
            else:
                entry["cost_usd"] += cost_usd

    def summary(self) -> dict[str, Any]:
        with self._lock:
            per_model = {model: dict(stats) for model, stats in self._per_model.items()}
        return {
            "run_id": self.run_id,
            "total_calls": sum(m["calls"] for m in per_model.values()),
            "total_input_tokens": sum(m["input_tokens"] for m in per_model.values()),
            "total_output_tokens": sum(m["output_tokens"] for m in per_model.values()),
            "total_cost_usd": round(sum(m["cost_usd"] for m in per_model.values()), 6),
            "per_model": per_model,
            "unpriced_models": sorted(self._unpriced_models),
        }


_current_tracker: ContextVar[CostTracker | None] = ContextVar("_current_tracker", default=None)


@contextmanager
def track_run(run_id: str) -> Iterator[CostTracker]:
    """Wrap one `run_question()` call. Every chat/embedding call made anywhere underneath — across
    LangGraph's parallel extractor branches — accumulates into the yielded tracker; logs the run
    total at INFO on exit either way."""
    tracker = CostTracker(run_id)
    token = _current_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _current_tracker.reset(token)
        summary = tracker.summary()
        logger.info(
            "[%s] token usage: %d calls, %d input + %d output tokens, $%.6f%s",
            run_id,
            summary["total_calls"],
            summary["total_input_tokens"],
            summary["total_output_tokens"],
            summary["total_cost_usd"],
            f" (unpriced: {', '.join(summary['unpriced_models'])})" if summary["unpriced_models"] else "",
        )


class RunCostLoggingCallback(BaseCallbackHandler):
    """Attached to every `ChatOpenAI` built by `clairfin.utils.llm.get_chat_llm`. Logs each
    completion's token usage/cost at INFO and, if a `track_run` is active on this contextvar chain,
    adds it to that run's totals."""

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        model = (response.llm_output or {}).get("model_name", "unknown")
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        if not usage:
            for generation_list in response.generations:
                for generation in generation_list:
                    meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
                    if meta:
                        input_tokens += meta.get("input_tokens", 0)
                        output_tokens += meta.get("output_tokens", 0)

        cost = _cost(model, input_tokens, output_tokens)
        logger.info(
            "LLM call model=%s input_tokens=%d output_tokens=%d cost_usd=%s",
            model,
            input_tokens,
            output_tokens,
            f"{cost:.6f}" if cost is not None else "unknown (no pricing entry)",
        )
        tracker = _current_tracker.get()
        if tracker is not None:
            tracker.record(model, input_tokens, output_tokens, cost)


def log_embedding_usage(model: str, texts: list[str]) -> None:
    """Embeddings never fire LangChain callbacks, so token counts here are estimated with
    `tiktoken` rather than read off an API response — OpenAI's embeddings response does carry a
    real `usage.total_tokens`, but `langchain_openai.OpenAIEmbeddings` doesn't surface it, and
    swapping to the raw SDK client just to get an exact count isn't worth it for a cost estimate."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding(_FALLBACK_ENCODING)
    input_tokens = sum(len(encoding.encode(text)) for text in texts)
    cost = _cost(model, input_tokens, 0)
    logger.info(
        "Embedding call model=%s texts=%d input_tokens=%d cost_usd=%s",
        model,
        len(texts),
        input_tokens,
        f"{cost:.6f}" if cost is not None else "unknown (no pricing entry)",
    )
    tracker = _current_tracker.get()
    if tracker is not None:
        tracker.record(model, input_tokens, 0, cost)
