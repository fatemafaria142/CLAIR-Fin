"""Standalone connectivity/quota check for the OpenAI account this project uses — reuses
`configs/settings.py` so it tests the exact same API key, chat model, and embedding model the
CLAIR-Fin pipeline calls, rather than a generic hard-coded example. Run this before kicking off a
real evaluation sweep whenever you've hit a 429 (rate limit / insufficient_quota) — it fails fast
and tells you which of {key invalid, quota exhausted, model unavailable} it actually is, instead of
burning a 20-question pipeline run to find out.

Usage:
    python -m scripts.check_openai_connection
"""

from __future__ import annotations

import sys

from openai import APIStatusError, AuthenticationError, OpenAI, RateLimitError

from configs.settings import get_settings


def main() -> int:
    settings = get_settings()
    client = OpenAI(api_key=settings.llm.openai_api_key.get_secret_value())

    print(f"Chat model:      {settings.llm.chat_model}")
    print(f"Embedding model: {settings.llm.embedding_model}")
    print()

    ok = True

    print("Checking chat completion...", end=" ")
    try:
        response = client.chat.completions.create(
            model=settings.llm.chat_model,
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
            max_tokens=5,
        )
        print(f"OK -> {response.choices[0].message.content!r}")
    except AuthenticationError:
        print("FAILED — API key is invalid or revoked.")
        ok = False
    except RateLimitError as exc:
        print("FAILED — rate limit or quota exceeded.")
        print(f"  {exc.message}")
        ok = False
    except APIStatusError as exc:
        print(f"FAILED — HTTP {exc.status_code}: {exc.message}")
        ok = False

    print("Checking embeddings...", end=" ")
    try:
        client.embeddings.create(model=settings.llm.embedding_model, input="pong")
        print("OK")
    except AuthenticationError:
        print("FAILED — API key is invalid or revoked.")
        ok = False
    except RateLimitError as exc:
        print("FAILED — rate limit or quota exceeded.")
        print(f"  {exc.message}")
        ok = False
    except APIStatusError as exc:
        print(f"FAILED — HTTP {exc.status_code}: {exc.message}")
        ok = False

    print()
    print("All checks passed — safe to re-run the evaluation." if ok else "Fix the above before re-running the evaluation.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
