"""Structured output schemas for per-page vision/native extraction: tables, chart readings, and the combined page result."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "line", "pie", "area", "scatter", "combo", "other"]


class ExtractedTable(BaseModel):
    caption: str | None = None
    columns: list[str]
    rows: list[list[str]]


class ChartValueReading(BaseModel):
    """One approximate value read off a chart, tied to a specific series — not a loose string.
    A prior version used `approx_key_values: list[str]`, which let the vision model satisfy the
    field with one value per *period* (e.g. one number for FY25) instead of one per
    *series-period pair* on a multi-series chart, silently collapsing two or three series down to
    one. Verified happening in practice (docs/implementation_mapping.md §8) — structuring this
    field forces the model to attribute every value to a named series."""

    series: str
    period: str
    approx_value: str


class ExtractedChart(BaseModel):
    caption: str | None = None
    chart_type: ChartType
    axis_labels: str
    series: list[str] = Field(default_factory=list)
    trend_description: str
    value_readings: list[ChartValueReading] = Field(default_factory=list)


class PageVisualExtraction(BaseModel):
    prose_text: str
    tables: list[ExtractedTable] = Field(default_factory=list)
    charts: list[ExtractedChart] = Field(default_factory=list)
