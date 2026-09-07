"""Ledger-guardian agent: materializes per-claim evidence into the custody ledger (text/table/chart nodes, cross-modal links, constraint checks) and decides fast-path vs. escalation."""
from __future__ import annotations

import re
import uuid

from clairfin.schemas.evidence import EvidenceItem
from clairfin.schemas.ledger import ChartRegion, ConstraintCheck, DerivedMetric, TableCell, TextSpan
from clairfin.schemas.state import GraphState
from clairfin.tools.aea_scorer import coverage_score
from configs.loaders import load_yaml
from configs.settings import get_settings


def _node_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _numeric_value(raw: str | None) -> float:
    """Strip a `tool_derived` value string down to its number, whatever unit suffix it carries
    ("12.34%", "+1.51 pp", "-4.39 pp"). Keeps only digits, '.', and a single leading sign."""
    cleaned = re.sub(r"[^0-9.\-]", "", raw or "0")
    try:
        return float(cleaned) if cleaned not in {"", "-", "."} else 0.0
    except ValueError:
        return 0.0


def _add_table_evidence(ledger, claim_id: str, item: EvidenceItem) -> str:
    if item.modality == "tool_derived":
        node_id = _node_id("metric")
        ledger.add_node(
            DerivedMetric(
                node_id=node_id,
                formula=item.formula or item.content,
                value=_numeric_value(item.value),
                inputs=[],
            )
        )
    else:
        node_id = _node_id("cell")
        ledger.add_node(
            TableCell(
                node_id=node_id,
                source=item.source,
                page=item.page,
                row_label=item.row_label or "",
                col_label=item.col_label or "",
                value=item.value or item.content,
                unit=item.unit,
            )
        )
    ledger.add_edge(node_id, claim_id, "SUPPORTS", score=item.confidence)
    return node_id


def _link_cross_modal(ledger, table_ids: list[str], chart_ids: list[str], text_ids: list[str]) -> None:
    """Heuristic linking: if a table cell's row label shows up in a chart's or text span's
    content, they likely describe the same underlying metric. Substring overlap, not semantic
    matching — a real but modest implementation, honestly scoped as such."""
    for table_id in table_ids:
        node = ledger.get_node(table_id)
        metric = (node.row_label or "").strip().lower()
        if len(metric) < 4:  # too short to be a meaningful, non-coincidental match
            continue
        for other_id in chart_ids + text_ids:
            other = ledger.get_node(other_id)
            other_text = (getattr(other, "description", None) or getattr(other, "text", "") or "").lower()
            if metric in other_text:
                ledger.add_edge(table_id, other_id, "SAME_AS")


def run(state: GraphState) -> dict:
    ledger = state["ledger"]
    claim = state["claim_tasks"][state["claim_index"]]
    modalities_present: set[str] = set()
    table_ids: list[str] = []
    chart_ids: list[str] = []
    text_ids: list[str] = []
    has_derived_metric = False

    for item in state.get("text_evidence", []):
        node_id = _node_id("span")
        ledger.add_node(
            TextSpan(node_id=node_id, source=item.source, page=item.page, text=item.content, hedge_type=item.hedge_type)
        )
        ledger.add_edge(node_id, claim.claim_id, "SUPPORTS", score=item.confidence)
        modalities_present.add("text")
        text_ids.append(node_id)

    for item in state.get("table_evidence", []):
        node_id = _add_table_evidence(ledger, claim.claim_id, item)
        modalities_present.add(item.modality)
        if item.modality == "tool_derived":
            has_derived_metric = True
        else:
            table_ids.append(node_id)

    for item in state.get("chart_evidence", []):
        node_id = _node_id("fig")
        ledger.add_node(
            ChartRegion(node_id=node_id, source=item.source, page=item.page, figure_id=node_id, description=item.content)
        )
        ledger.add_edge(node_id, claim.claim_id, "SUPPORTS", score=item.confidence)
        modalities_present.add("chart")
        chart_ids.append(node_id)

    _link_cross_modal(ledger, table_ids, chart_ids, text_ids)

    if claim.claim_type == "RATIO_IDENTITY":
        check_id = _node_id("check")
        if has_derived_metric:
            ledger.add_node(
                ConstraintCheck(
                    node_id=check_id,
                    name="ratio_identity_computed",
                    passed=True,
                    severity="SOFT_WARN",
                    detail="A RATIO_IDENTITY claim had a calculator-derived metric available for grounding.",
                )
            )
        else:
            ledger.add_node(
                ConstraintCheck(
                    node_id=check_id,
                    name="ratio_identity_computed",
                    passed=False,
                    severity="SOFT_WARN",
                    detail="A RATIO_IDENTITY claim had no calculator-derived metric — no two comparable "
                    "table cells were grounded for it, so any ratio in the eventual answer is not "
                    "independently verified.",
                )
            )
        ledger.add_edge(check_id, claim.claim_id, "VIOLATES_CONSTRAINT" if not has_derived_metric else "SUPPORTS")

    agreement = coverage_score(claim.claim_type, modalities_present)
    threshold = load_yaml(get_settings().agent_budgets_path)["orchestrator"]["fast_path_agreement_threshold"]

    return {"escalate": agreement < threshold, "agreement_score": agreement}
