"""Shared evidence-item schema produced by the extraction agents before it's materialized into ledger nodes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Modality = Literal["text", "table", "chart", "tool_derived"]
HedgeType = Literal["fact", "hedge", "attribution"]


class EvidenceItem(BaseModel):
    modality: Modality
    source: str
    page: int
    content: str
    confidence: float = 0.0

    row_label: str | None = None
    col_label: str | None = None
    value: str | None = None
    unit: str | None = None
    formula: str | None = None

    hedge_type: HedgeType | None = None
