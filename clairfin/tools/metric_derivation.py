from __future__ import annotations

import re

from clairfin.schemas.evidence import EvidenceItem
from clairfin.tools.calculator import percentage_point_diff, yoy_growth


def parse_number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_rate_unit(unit: str | None) -> bool:
    """True when a cell's own unit marks it as already a percentage/rate (e.g. an inflation rate
    or a GDP growth rate) rather than a level (e.g. BDT billion, USD million, basis points). For
    rate units, "how much did X change" is ambiguous between a percentage-point difference (plain
    subtraction) and a relative percent change (yoy_growth) — both are computed so the drafting
    agent can pick the one the claim actually asks for."""
    if not unit:
        return False
    return "percent" in unit.lower() or "%" in unit


def derive_metrics(cells: list[EvidenceItem]) -> list[EvidenceItem]:
    """When exactly two grounded values share a metric (row label) across two periods (column
    label), compute the period-over-period change deterministically rather than leaving it to an
    LLM to guess. For rate/percentage metrics, computes BOTH the percentage-point difference and
    the relative YoY growth, since the two answer different questions and the metric's own unit
    doesn't disambiguate which one a claim is asking for.

    Keyed by (source, page, row_label), not row_label alone — the same metric label can legitimately
    appear in more than one place in the document with different values; grouping by label alone
    risks pairing one location's earlier value with a different location's later value for the
    "same" metric.
    """
    by_metric: dict[tuple[str, int, str], list[EvidenceItem]] = {}
    for cell in cells:
        if cell.row_label:
            by_metric.setdefault((cell.source, cell.page, cell.row_label), []).append(cell)

    derived: list[EvidenceItem] = []
    for (_source, _page, metric), items in by_metric.items():
        if len(items) != 2:
            continue
        earlier, later = sorted(items, key=lambda c: c.col_label or "")
        previous, current = parse_number(earlier.value or ""), parse_number(later.value or "")
        if previous is None or current is None:
            continue
        confidence = min(earlier.confidence, later.confidence)
        col_range = f"{earlier.col_label}->{later.col_label}"

        if is_rate_unit(earlier.unit) or is_rate_unit(later.unit):
            pp_diff = percentage_point_diff(current, previous)
            derived.append(
                EvidenceItem(
                    modality="tool_derived",
                    source=later.source,
                    page=later.page,
                    content=(
                        f"Percentage-point change of '{metric}' from {earlier.col_label} to "
                        f"{later.col_label}: {float(pp_diff):+.2f} percentage points "
                        f"(computed as {current} - {previous}, both already percentages)"
                    ),
                    confidence=confidence,
                    row_label=metric,
                    col_label=col_range,
                    value=f"{float(pp_diff):+.2f} pp",
                    formula=f"{current} - {previous}",
                )
            )

        try:
            growth = yoy_growth(current, previous)
        except ZeroDivisionError:
            continue
        derived.append(
            EvidenceItem(
                modality="tool_derived",
                source=later.source,
                page=later.page,
                content=(
                    f"Relative growth of '{metric}' from {earlier.col_label} to {later.col_label}: "
                    f"{float(growth) * 100:.2f}% (computed from {previous} to {current})"
                ),
                confidence=confidence,
                row_label=metric,
                col_label=col_range,
                value=f"{float(growth) * 100:.2f}%",
                formula=f"({current} - {previous}) / {previous}",
            )
        )
    return derived
