"""Native table extraction from PDF pages, systematic-misread detection, and rendering tables to markdown / per-row retrieval text."""
from __future__ import annotations

import re

import fitz

from clairfin.schemas.extraction import ExtractedTable

_SECTION_LABEL = re.compile(r"^\s*\d+[.)]\s")  # "1. Agriculture", "2) Industry"
_SUBITEM_LABEL = re.compile(r"^\s*[a-z][.)]\s", re.IGNORECASE)  # "a) Crops and horticulture"


def has_duplicated_section_total(table: ExtractedTable) -> bool:
    """Detects one specific, real, repeatedly-observed vision-extraction failure: a section/
    category row ("1. Agriculture") that should carry its own aggregate total ends up with the
    exact same values as one of its sub-items ("a) Crops and horticulture") instead — i.e. the
    model duplicated a child row's reading into the parent instead of reading the parent's own
    total. This is never valid: a section total identical to a proper subset of its own sub-items
    is a logical impossibility whenever there's more than one sub-item with a nonzero value. Used
    to force `extraction_confidence: "low"` even when the self-consistency check (repeated calls
    agreeing with each other) would otherwise call this "high" — consensus across repeated calls of
    the same model catches random noise, not this kind of systematic misreading, which several
    independent attempts can all still agree on."""
    section_rows = [row for row in table.rows if row and _SECTION_LABEL.match(row[0])]
    subitem_rows = [row for row in table.rows if row and _SUBITEM_LABEL.match(row[0])]
    if not section_rows or not subitem_rows:
        return False
    return any(section[1:] == sub[1:] for section in section_rows for sub in subitem_rows if section[1:])


def extract_native_tables(page: fitz.Page) -> list[ExtractedTable]:
    """Cheap path: only used when PyMuPDF's own detection actually finds something."""
    found = page.find_tables()
    tables: list[ExtractedTable] = []
    for table in found.tables:
        rows = table.extract()
        if not rows:
            continue
        columns = [str(cell) if cell is not None else "" for cell in rows[0]]
        body = [[str(cell) if cell is not None else "" for cell in row] for row in rows[1:]]
        tables.append(ExtractedTable(caption=None, columns=columns, rows=body))
    return tables


def table_to_markdown(table: ExtractedTable) -> str:
    """Render a table as markdown — both the text embedded for retrieval and the form the
    Tabular Evidence Agent reads when grounding a specific cell against a claim."""
    lines: list[str] = []
    if table.caption:
        lines.append(f"**{table.caption}**")
    lines.append("| " + " | ".join(table.columns) + " |")
    lines.append("| " + " | ".join("---" for _ in table.columns) + " |")
    for row in table.rows:
        padded = (row + [""] * len(table.columns))[: len(table.columns)]
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def table_row_to_text(table: ExtractedTable, row: list[str]) -> str:
    """One row rendered as a compact, self-contained statement — a much more precise retrieval
    unit than embedding the whole table as a single blob. A whole-table embedding for a table with
    15+ rows (common in this document: sectoral breakdowns, multi-country WEO tables, CPI
    sub-groups) gets diluted by every OTHER row's content, so a query about one specific metric
    (e.g. "agriculture's share of GDP") competes for relevance against industry, services, and every
    sub-sector row in the same vector. A per-row document lets that exact row win retrieval on its
    own terms. Emitted *in addition to* the whole-table document (`table_to_markdown`), not instead
    of it — multi-hop questions that need the table's overall structure still need the full view."""
    row_label = row[0] if row else ""
    padded = (row + [""] * len(table.columns))[: len(table.columns)]
    pairs = [f"{col}={val}" for col, val in zip(table.columns[1:], padded[1:]) if val]
    prefix = f"{table.caption} — " if table.caption else ""
    return f"{prefix}{row_label}: " + ", ".join(pairs) if pairs else f"{prefix}{row_label}"
