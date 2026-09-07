"""Sentence-aware text chunking for ingestion: splits prose into overlapping, target-sized chunks without cutting a sentence in half."""
from __future__ import annotations

import re

# Split after sentence-ending punctuation followed by whitespace and a capital letter/digit/quote
# — good enough for the prose this pipeline targets (financial narrative, not abbreviation-dense
# legal text) without pulling in a full sentence tokenizer dependency.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])")

# Common abbreviations that would otherwise be mistaken for sentence ends right before a capital
# letter (e.g. "...as of Dec. 2025..."). Kept short and finance/report-domain relevant.
_ABBREVIATIONS = {"mr.", "mrs.", "dr.", "vs.", "etc.", "e.g.", "i.e.", "no.", "fig.", "approx.", "dec.", "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.", "oct.", "nov."}


def split_sentences(text: str) -> list[str]:
    """Split `text` into sentences, protecting common abbreviations from false splits."""
    raw_sentences = _SENTENCE_BOUNDARY.split(text.strip())
    sentences: list[str] = []
    buffer = ""
    for raw in raw_sentences:
        buffer = f"{buffer} {raw}".strip() if buffer else raw
        last_word = buffer.rsplit(" ", 1)[-1].lower()
        if last_word in _ABBREVIATIONS:
            continue  # keep accumulating — this wasn't really a sentence end
        sentences.append(buffer)
        buffer = ""
    if buffer:
        sentences.append(buffer)
    return [s for s in sentences if s.strip()]


def chunk_text(text: str, *, target_chars: int = 1000, overlap_sentences: int = 2) -> list[str]:
    """Group sentences into ~`target_chars`-sized chunks, never splitting a sentence, carrying
    the last `overlap_sentences` sentences of each chunk into the start of the next."""
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > target_chars:
            chunks.append(" ".join(current))
            current = current[-overlap_sentences:] if overlap_sentences else []
            current_len = sum(len(s) + 1 for s in current)
        current.append(sentence)
        current_len += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks
