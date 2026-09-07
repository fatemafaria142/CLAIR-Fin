"""PDF page heuristics for detecting native/scanned chart signals, and rendering extracted chart structure into citable prose."""
from __future__ import annotations

import fitz

from clairfin.schemas.extraction import ExtractedChart

# A page with at least this many vector drawing paths (axes, gridlines, bars, data points)
# plausibly has a *natively drawn* chart on it; fewer are more likely table rules, underlines, or
# logos. Calibrated against data/Chapter_1.pdf: text-only pages have 0 drawing paths, its three
# real charts sit on pages with 93-160.
CHART_SIGNAL_MIN_DRAWING_PATHS = 15

# A raster image occupying at least this fraction of the page is large enough to plausibly be a
# *scanned* table or chart pasted into an otherwise-native page — not a small logo, letterhead, or
# decorative rule (measured at ~0.004 of page area for a real header banner in
# data/Chapter_1.pdf). Drawing-path count is useless here: a scanned image has none of its own —
# it's one opaque raster blob, however complex the table/chart baked into it actually is.
IMAGE_SIGNAL_MIN_AREA_RATIO = 0.05


def has_chart_signal(page: fitz.Page) -> bool:
    """Catches natively vector-drawn charts (e.g. matplotlib/Excel-exported)."""
    return len(page.get_drawings()) >= CHART_SIGNAL_MIN_DRAWING_PATHS


def has_image_signal(page: fitz.Page) -> bool:
    """Catches a scanned table, chart, or photo large enough to matter — a page can have a
    perfectly trustworthy native text layer and still be hiding a scanned table pasted in as a
    picture; drawing-path count won't see that, only image geometry will."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for image_info in page.get_image_info():
        bbox = image_info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area / page_area >= IMAGE_SIGNAL_MIN_AREA_RATIO:
            return True
    return False


def chart_to_description(chart: ExtractedChart) -> str:
    """Render a chart's extracted structure as embeddable, citable prose. Approximate values stay
    explicitly labeled as approximate — the Asymmetric Evidence Authority
    (`clairfin/tools/aea_scorer.py`) is only justified in never letting a chart outvote an exact
    table cell if the chart's own text is honest about being an approximation in the first place.

    Value readings are grouped by series, not dumped as one flat list — on a multi-series chart
    this is what actually keeps each series' numbers distinguishable in the embedded text (and
    later, in what the Visual Evidence Agent reads), rather than an unattributed list a reader
    can't tell apart.
    """
    parts: list[str] = []
    if chart.caption:
        parts.append(chart.caption)
    parts.append(f"A {chart.chart_type} chart ({chart.axis_labels}).")
    if chart.series:
        parts.append(f"Series: {', '.join(chart.series)}.")
    parts.append(chart.trend_description)

    if chart.value_readings:
        by_series: dict[str, list[str]] = {}
        for reading in chart.value_readings:
            value = _strip_approximation_hedge(reading.approx_value)
            by_series.setdefault(reading.series, []).append(f"{reading.period}: approximately {value}")
        series_summaries = [f"{series} ({'; '.join(readings)})" for series, readings in by_series.items()]
        parts.append("Approximate values read from the chart, by series: " + "; ".join(series_summaries) + ".")

    return " ".join(parts)


_APPROXIMATION_HEDGES = ("approximately ", "approx. ", "approx ", "around ", "~")


def _strip_approximation_hedge(value: str) -> str:
    """The extraction prompt asks for a bare number (e.g. "24 percent"), but the model sometimes
    includes its own hedge word anyway ("approximately 24 percent") — this function's own caller
    already adds "approximately" once, so without stripping, values doubled up as "approximately
    approximately 24 percent". Strip any hedge the model added; the caller's own wording carries
    the approximation, once."""
    stripped = value.strip()
    lowered = stripped.lower()
    for hedge in _APPROXIMATION_HEDGES:
        if lowered.startswith(hedge):
            return stripped[len(hedge):].strip()
    return stripped
