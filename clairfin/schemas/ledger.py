"""Ledger node/edge type definitions (claim, evidence nodes, constraint checks) and their JSON (de)serialization for ClaimLedger."""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel

from clairfin.schemas.claim import ClaimType
from clairfin.schemas.evidence import HedgeType

NodeType = Literal["claim", "text_span", "table_cell", "derived_metric", "chart_region", "constraint_check"]
EdgeRelation = Literal[
    "SUPPORTS", "CONTRADICTS", "SAME_AS", "QUANTIFIES", "VISUALIZES", "ELABORATES",
    "DERIVED_FROM", "VIOLATES_CONSTRAINT",
]
Severity = Literal["HARD_FAIL", "SOFT_WARN"]


class Claim(BaseModel):
    node_id: str
    node_type: Literal["claim"] = "claim"
    question: str
    claim_type: ClaimType
    text: str


class TextSpan(BaseModel):
    node_id: str
    node_type: Literal["text_span"] = "text_span"
    source: str
    page: int
    text: str
    hedge_type: HedgeType | None = None


class TableCell(BaseModel):
    node_id: str
    node_type: Literal["table_cell"] = "table_cell"
    source: str
    page: int
    row_label: str
    col_label: str
    value: str
    unit: str | None = None


class DerivedMetric(BaseModel):
    node_id: str
    node_type: Literal["derived_metric"] = "derived_metric"
    formula: str
    value: float
    inputs: list[str] = []


class ChartRegion(BaseModel):
    node_id: str
    node_type: Literal["chart_region"] = "chart_region"
    source: str
    page: int
    figure_id: str
    description: str


class ConstraintCheck(BaseModel):
    node_id: str
    node_type: Literal["constraint_check"] = "constraint_check"
    name: str
    passed: bool
    severity: Severity
    detail: str


LedgerNode = Union[Claim, TextSpan, TableCell, DerivedMetric, ChartRegion, ConstraintCheck]

_NODE_TYPE_MAP: dict[str, type[BaseModel]] = {
    "claim": Claim,
    "text_span": TextSpan,
    "table_cell": TableCell,
    "derived_metric": DerivedMetric,
    "chart_region": ChartRegion,
    "constraint_check": ConstraintCheck,
}


def node_from_dict(data: dict) -> LedgerNode:
    model = _NODE_TYPE_MAP[data["node_type"]]
    return model.model_validate(data)
