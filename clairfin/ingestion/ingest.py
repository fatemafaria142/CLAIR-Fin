"""PDF ingestion pipeline: extracts text/table/chart content per page (native or vision fallback with consensus checks), chunks it, and upserts it into the Milvus vector store."""
from __future__ import annotations

import base64
import json
import logging
import traceback
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_milvus import Milvus
from tqdm import tqdm

from clairfin.ingestion.chunking import chunk_text
from clairfin.ingestion.figures import chart_to_description, has_chart_signal, has_image_signal
from clairfin.ingestion.manual_corrections import TABLE_OVERRIDES
from clairfin.ingestion.tables import (
    extract_native_tables,
    has_duplicated_section_total,
    table_row_to_text,
    table_to_markdown,
)
from clairfin.schemas.extraction import ExtractedTable, PageVisualExtraction
from clairfin.utils.llm import get_chat_llm, get_embeddings
from clairfin.utils.logging_setup import configure_logging
from clairfin.utils.prompts import load_prompt
from configs.settings import Settings, get_settings

logger = logging.getLogger(__name__)

CHUNK_TARGET_CHARS = 1000
CHUNK_OVERLAP_SENTENCES = 2

# Some PDFs (seen in `data/Chapter_1.pdf`) embed a broken/scrambled font encoding that PyMuPDF
# decodes into mostly control characters instead of real text — the page renders fine visually,
# but `get_text()` is garbage. Past this fraction of non-space control characters, don't trust it.
GARBLED_CONTROL_CHAR_RATIO = 0.15


def _looks_garbled(text: str) -> bool:
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return False
    control_chars = sum(1 for c in non_space if ord(c) < 32 or ord(c) == 127)
    return (control_chars / len(non_space)) > GARBLED_CONTROL_CHAR_RATIO


VLM_RENDER_DPI = 300  # raised from 200 after testing found small dense table text unreadable at 200


def _extract_page_via_vlm(page: fitz.Page, *, model: str) -> PageVisualExtraction:
    """One structured call: prose text, tables, and charts together — see
    `prompts/tools/page_visual_extraction.md`. `model` is chosen by the caller per page
    (`extract_page_content`) — the stronger, pricier `settings.llm.vision_model` for pages with a
    chart/scanned-image signal (where dense numeric reading actually matters and mini was verified
    to misread tables mini couldn't get right), the cheap `settings.llm.chat_model` for pages that
    only need vision because of this document's broken font layer but have no chart/image content
    to misread — plain prose transcription doesn't need the stronger model."""
    pixmap = page.get_pixmap(dpi=VLM_RENDER_DPI)
    image_b64 = base64.b64encode(pixmap.tobytes("png")).decode("utf-8")

    llm = get_chat_llm(temperature=0, model=model).with_structured_output(PageVisualExtraction)
    message = HumanMessage(
        content=[
            {"type": "text", "text": load_prompt("tools/page_visual_extraction")},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]
    )
    result = llm.invoke([message])
    assert isinstance(result, PageVisualExtraction)
    return result


def _tables_agree(a: list[ExtractedTable], b: list[ExtractedTable], min_cell_agreement: float = 0.8) -> bool:
    """Cheap agreement check between two independent extraction attempts on the same page: same
    number of tables, same shape, and most cells identical. Not exact-match — minor wording drift
    in a caption is fine, but the actual numbers need to agree."""
    if len(a) != len(b):
        return False
    for table_a, table_b in zip(a, b):
        if table_a.columns != table_b.columns or len(table_a.rows) != len(table_b.rows):
            return False
        total = matches = 0
        for row_a, row_b in zip(table_a.rows, table_b.rows):
            for cell_a, cell_b in zip(row_a, row_b):
                total += 1
                if cell_a.strip() == cell_b.strip():
                    matches += 1
        if total == 0 or (matches / total) < min_cell_agreement:
            return False
    return True


def _extract_tables_with_consensus(
    page: fitz.Page, settings: Settings, first_pass: PageVisualExtraction
) -> tuple[list[ExtractedTable], bool]:
    """Table extraction is verifiably the least reliable part of vision extraction: repeated
    testing found the *same* table, from the *same* page, at temperature=0, producing different
    values across separate calls.

    IMPORTANT, verified limitation of this function itself: agreement across repeated attempts
    means the *same reproducible reading*, not a *correct* one. Tested directly against
    `data/Chapter_1.pdf` page 4's Table 1.02: two independent attempts consistently agreed on a
    fabricated, drastically over-simplified 3-row table that doesn't match the source at all
    (verified by rendering the page and reading it directly, and cross-checked against the page's
    own prose restating the real figures) — a case where the model has a *systematic* tendency
    (collapsing a long, detailed table into a tidy summary) rather than *random* per-call noise.
    Self-consistency across repeated calls of the same model catches the latter, not the former —
    this is a real, demonstrated gap in this mechanism, not a hypothetical caveat. Treat
    `extraction_confidence: "high"` as "reproducible across attempts," never as "verified against
    ground truth" — nothing in this codebase currently verifies that. A stronger mitigation
    (cross-checking a table's figures against prose on the same page that restates them, which
    financial reports commonly do) is identified but not implemented — see
    docs/implementation_mapping.md §8 and §10.

    Costs up to 2 extra vision calls, but only for pages that already needed a vision pass *and*
    found at least one table on it — most pages hit neither condition.
    """
    if not first_pass.tables:
        return [], True

    second_pass = _extract_page_via_vlm(page, model=settings.llm.vision_model)
    if _tables_agree(first_pass.tables, second_pass.tables):
        return first_pass.tables, True

    third_pass = _extract_page_via_vlm(page, model=settings.llm.vision_model)
    if _tables_agree(first_pass.tables, third_pass.tables):
        return first_pass.tables, True
    if _tables_agree(second_pass.tables, third_pass.tables):
        return second_pass.tables, True

    logger.warning(
        "Table extraction did not reach consensus across 3 independent attempts — keeping the "
        "first attempt but flagging it low-confidence."
    )
    return first_pass.tables, False


def extract_page_content(page: fitz.Page, settings: Settings) -> tuple[PageVisualExtraction, bool]:
    """Text via native extraction when trustworthy, else the vision fallback. Tables via native
    detection when it finds any (trusted fully — no consensus check needed for a deterministic
    parser), else the vision fallback with a self-consistency check (`_extract_tables_with_consensus`).
    Charts always via the vision fallback (no native chart-semantics extraction exists) — attempted
    when the page shows either a natively-drawn chart signal (`has_chart_signal`, vector drawing
    paths) or a large embedded image (`has_image_signal`) that could be a *scanned* table, chart,
    or photo.

    Returns `(extraction, tables_confident)` — `tables_confident` is `True` for native-table pages
    and vision pages where table extraction reached consensus, `False` when it didn't (still
    usable, just flagged for callers to treat with appropriately less trust).
    """
    native_text = page.get_text().strip()
    text_ok = bool(native_text) and not _looks_garbled(native_text)
    chart_signal = has_chart_signal(page)
    image_signal = has_image_signal(page)

    if text_ok and not chart_signal and not image_signal:
        return PageVisualExtraction(prose_text=native_text, tables=extract_native_tables(page), charts=[]), True

    has_visible_content = native_text or page.get_images() or page.get_drawings()
    if not has_visible_content:
        return PageVisualExtraction(prose_text="", tables=[], charts=[]), True

    # A chart/scanned-image signal means this page plausibly has dense numeric content worth the
    # stronger (pricier) model; a page needing vision purely because of the broken font layer, with
    # no chart/image signal at all, is a plain prose transcription task — the cheap chat model
    # handles that fine, no need to pay vision-model prices for it.
    needs_strong_model = chart_signal or image_signal
    page_model = settings.llm.vision_model if needs_strong_model else settings.llm.chat_model
    logger.info(
        "Page needs a vision pass (text_ok=%s, chart_signal=%s, image_signal=%s) — using %s",
        text_ok,
        chart_signal,
        image_signal,
        page_model,
    )
    vlm_result = _extract_page_via_vlm(page, model=page_model)
    text = native_text if text_ok else vlm_result.prose_text

    page_number = page.number + 1
    if page_number in TABLE_OVERRIDES:
        # A human-verified correction exists for this exact page — see
        # clairfin/ingestion/manual_corrections.py for why and how it was produced. Takes priority
        # over both native and vision extraction; confidence is True because it was checked against
        # the actual rendered page image, not because any automated extraction agreed with itself.
        return PageVisualExtraction(prose_text=text, tables=TABLE_OVERRIDES[page_number], charts=vlm_result.charts), True

    native_tables = extract_native_tables(page)
    if native_tables:
        tables, tables_confident = native_tables, True
    else:
        tables, tables_confident = _extract_tables_with_consensus(page, settings, vlm_result)

    if tables_confident and any(has_duplicated_section_total(t) for t in tables):
        logger.warning(
            "Page %d: a table's section-total row exactly duplicates one of its own sub-item "
            "rows — a known systematic misread, not caught by consensus. Downgrading to low "
            "confidence.",
            page.number + 1,
        )
        tables_confident = False

    return PageVisualExtraction(prose_text=text, tables=tables, charts=vlm_result.charts), tables_confident


def chunk_pdf(pdf_path: Path) -> list[Document]:
    """Sentence-aware text chunks, one document per table, one document per chart — all tagged
    with `source`/`page`/`modality` metadata for citation and retrieval filtering later."""
    settings = get_settings()
    documents: list[Document] = []

    with fitz.open(pdf_path) as doc:
        page_progress = tqdm(doc, desc=f"  {pdf_path.name} pages", leave=False)
        for page_number, page in enumerate(page_progress, start=1):
            content, tables_confident = extract_page_content(page, settings)
            if not (content.prose_text or content.tables or content.charts):
                continue  # genuinely blank page

            text_chunks = chunk_text(
                content.prose_text, target_chars=CHUNK_TARGET_CHARS, overlap_sentences=CHUNK_OVERLAP_SENTENCES
            )
            for chunk_index, chunk in enumerate(text_chunks):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "source": pdf_path.name,
                            "page": page_number,
                            "chunk": chunk_index,
                            "modality": "text",
                        },
                    )
                )

            for table_index, table in enumerate(content.tables):
                confidence = "high" if tables_confident else "low"
                documents.append(
                    Document(
                        page_content=table_to_markdown(table),
                        metadata={
                            "source": pdf_path.name,
                            "page": page_number,
                            "chunk": table_index,
                            "modality": "table",
                            "caption": table.caption or "",
                            "columns": json.dumps(table.columns),
                            "rows": json.dumps(table.rows),
                            "extraction_confidence": confidence,
                        },
                    )
                )
                # Also embed one document per row. A whole-table embedding for a table with many
                # rows (common here: sectoral breakdowns, multi-country comparisons, CPI
                # sub-groups) gets diluted by every other row's content, so a query about one
                # specific metric competes against the whole table instead of matching precisely.
                # Row docs share the table's chunk-id namespace via a large offset so they can
                # never collide with the whole-table doc's id (`c{table_index}`) or another
                # table's row docs on the same page.
                if len(table.rows) > 1:
                    for row_index, row in enumerate(table.rows):
                        if not (row and row[0].strip()):
                            continue  # no row label — nothing to precisely retrieve by
                        documents.append(
                            Document(
                                page_content=table_row_to_text(table, row),
                                metadata={
                                    "source": pdf_path.name,
                                    "page": page_number,
                                    "chunk": 1000 + table_index * 100 + row_index,
                                    "modality": "table",
                                    "caption": table.caption or "",
                                    "columns": json.dumps(table.columns),
                                    "rows": json.dumps([row]),
                                    "row_label": row[0],
                                    "extraction_confidence": confidence,
                                },
                            )
                        )

            for chart_index, chart in enumerate(content.charts):
                documents.append(
                    Document(
                        page_content=chart_to_description(chart),
                        metadata={
                            "source": pdf_path.name,
                            "page": page_number,
                            "chunk": chart_index,
                            "modality": "chart",
                            "caption": chart.caption or "",
                            "chart_type": chart.chart_type,
                            "series": json.dumps(chart.series),
                            "value_readings": json.dumps([r.model_dump() for r in chart.value_readings]),
                        },
                    )
                )

            logger.info(
                "Page %d of %s: %d text chunk(s), %d table(s), %d chart(s)",
                page_number,
                pdf_path.name,
                len(text_chunks),
                len(content.tables),
                len(content.charts),
            )

    return documents


@lru_cache
def get_vector_store() -> Milvus:
    """Cached: the agent graph queries this from multiple parallel threads
    (clairfin/graph/build.py's extract_text/extract_table/extract_chart fan-out) — one shared
    client instance, reused by every caller, avoids each thread racing to open its own connection.

    `metadata_field="metadata"` — deliberately *not* `enable_dynamic_field=True`, which was tried
    first and found broken across process restarts: the LangChain wrapper only requests dynamic
    fields it has locally tracked *in the current process* (`self._dynamic_fields`), which is
    empty for a fresh script that never called `add_texts` itself — every retrieval came back with
    empty metadata. An explicit `metadata_field` is a real, described schema field, always
    included in search results regardless of process history. Every modality's extra metadata
    (`source`/`page`/`modality`/`caption`/... — text, table, and chart documents don't all carry
    the same fields) lives inside that one JSON field; filtering uses `metadata["key"]` path syntax
    (`clairfin/retrieval/retriever.py`), not top-level field names.
    """
    settings = get_settings()
    return Milvus(
        embedding_function=get_embeddings(),
        collection_name=settings.milvus.collection_name,
        connection_args={"uri": settings.milvus.uri},
        metadata_field="metadata",
        auto_id=False,
    )


def ingest(pdf_paths: list[Path] | None = None) -> int:
    """Chunk + embed + upsert the given PDFs (default: every PDF in `data/`). Returns chunk count.

    Chunk IDs are deterministic (`{filename}-p{page}-{modality}-c{chunk}`), so re-running this on
    the same PDF upserts (`Milvus.upsert`) rather than duplicating entries.
    """
    settings = get_settings()
    pdf_paths = pdf_paths if pdf_paths is not None else settings.paths.source_pdfs()
    if not pdf_paths:
        logger.warning("No PDFs found in %s — nothing to ingest.", settings.paths.data_dir)
        return 0

    store = get_vector_store()
    total = 0
    for pdf_path in tqdm(pdf_paths, desc="Ingesting PDFs"):
        documents = chunk_pdf(pdf_path)
        if not documents:
            logger.warning("No extractable content in %s", pdf_path.name)
            continue
        ids = [
            f"{pdf_path.stem}-p{d.metadata['page']}-{d.metadata['modality']}-c{d.metadata['chunk']}"
            for d in documents
        ]
        if store.col is None:
            # First write to a fresh Milvus Lite file — the collection doesn't exist yet, and
            # `upsert()` requires it to. `add_documents` creates it from this batch's schema.
            store.add_documents(documents, ids=ids)
        else:
            store.upsert(ids=ids, documents=documents)
        total += len(documents)
        logger.info("Ingested %d chunks from %s", len(documents), pdf_path.name)

    return total


class _SuppressBenignAllocTimestampError(logging.Filter):
    """Milvus Lite's embedded grpc server doesn't implement `AllocTimestamp` (a call the pymilvus
    client makes after `create_index`, e.g. via `Milvus._create_index` in langchain_milvus). The
    client already handles this gracefully — `pymilvus.client.grpc_handler.alloc_timestamp` is
    wrapped in `@ignore_unimplemented(0)`, so it just returns 0 — but grpc's own server-side
    handler still logs a full traceback for the swallowed exception on every ingest run. Harmless;
    just noisy. Filter that one traceback, not the whole `grpc._server` logger, so real server-side
    errors still surface.

    The log message itself is just `"Exception calling application: Method not implemented!"` —
    the string "AllocTimestamp" only shows up inside the attached traceback (`record.exc_info`),
    not the message text, so matching has to look at the traceback's frames rather than
    `record.getMessage()`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None:
            return True
        exc_type, exc_value, exc_tb = record.exc_info
        if exc_type is not NotImplementedError:
            return True
        return not any(frame.name == "AllocTimestamp" for frame in traceback.extract_tb(exc_tb))


logging.getLogger("grpc._server").addFilter(_SuppressBenignAllocTimestampError())


def main() -> None:
    configure_logging("ingest")
    settings = get_settings()
    settings.paths.ensure_runtime_dirs()
    count = ingest()
    logger.info(
        "Done. %d chunks in collection '%s' at %s",
        count,
        settings.milvus.collection_name,
        settings.milvus.uri,
    )


if __name__ == "__main__":
    main()
