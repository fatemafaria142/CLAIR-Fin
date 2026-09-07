"""Wires the CLAIR-Fin agents into the LangGraph state machine: per-claim extraction, coverage-gated debate/custody routing, and final audited publication."""
from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from clairfin.agents import (
    adversarial_counsel,
    affirmative_counsel,
    brief_synthesizer,
    custody_verifier,
    judge_auditor,
    ledger_guardian,
    narrative_evidence,
    planner_orchestrator,
    tabular_evidence,
    visual_evidence,
)
from clairfin.schemas.state import GraphState
from configs.loaders import load_yaml
from configs.settings import get_settings

logger = logging.getLogger(__name__)


def _current_claim_id(state: GraphState) -> str:
    return state["claim_tasks"][state["claim_index"]].claim_id


def _route_after_guard(state: GraphState) -> str:
    route = "debate_affirmative" if state.get("escalate") else "judge_audit"
    logger.info("[%s] ledger_guard: coverage=%.2f -> %s", _current_claim_id(state), state.get("agreement_score", 0.0), route)
    return route


def _route_after_custody(state: GraphState) -> str:
    if state.get("custody_broken"):
        route = "judge_audit"
    elif state.get("custody_repair_note"):
        route = "debate_affirmative"
    else:
        route = "debate_adversarial"
    logger.info("[%s] custody_verify -> %s", _current_claim_id(state), route)
    return route


def _route_after_adversarial(state: GraphState) -> str:
    if state.get("recommend_abstain"):
        route = "judge_audit"
    else:
        findings = state.get("adversarial_findings") or []
        if not any(f["severity"] == "high" for f in findings):
            route = "judge_audit"
        else:
            max_rounds = load_yaml(get_settings().agent_budgets_path)["orchestrator"]["max_debate_rounds_per_claim"]
            route = "debate_affirmative" if state.get("debate_round", 0) < max_rounds else "judge_audit"
    logger.info(
        "[%s] debate_adversarial: round=%d, findings=%d -> %s",
        _current_claim_id(state),
        state.get("debate_round", 0),
        len(state.get("adversarial_findings") or []),
        route,
    )
    return route


def _route_after_judge(state: GraphState) -> str:
    has_more_claims = state["claim_index"] + 1 < len(state["claim_tasks"])
    return "advance_claim" if has_more_claims else "publish"


def _advance_claim(state: GraphState) -> dict:
    return {
        "claim_index": state["claim_index"] + 1,
        "text_evidence": [],
        "table_evidence": [],
        "chart_evidence": [],
        "affirmative_brief": None,
        "adversarial_findings": [],
        "recommend_abstain": False,
        "escalate": False,
        "debate_round": 0,
        "custody_broken": False,
        "custody_repair_attempts": 0,
        "custody_repair_note": None,
    }


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(GraphState)

    graph.add_node("plan_control", planner_orchestrator.run)
    graph.add_node("extract_text", narrative_evidence.run)
    graph.add_node("extract_table", tabular_evidence.run)
    graph.add_node("extract_chart", visual_evidence.run)
    graph.add_node("ledger_guard", ledger_guardian.run)
    graph.add_node("debate_affirmative", affirmative_counsel.run)
    graph.add_node("custody_verify", custody_verifier.run)
    graph.add_node("debate_adversarial", adversarial_counsel.run)
    graph.add_node("judge_audit", judge_auditor.run)
    graph.add_node("advance_claim", _advance_claim)
    graph.add_node("publish", brief_synthesizer.run)

    graph.set_entry_point("plan_control")

    for extractor in ("extract_text", "extract_table", "extract_chart"):
        graph.add_edge("plan_control", extractor)
        graph.add_edge("advance_claim", extractor)
        graph.add_edge(extractor, "ledger_guard")

    graph.add_conditional_edges(
        "ledger_guard", _route_after_guard, {"debate_affirmative": "debate_affirmative", "judge_audit": "judge_audit"}
    )
    graph.add_edge("debate_affirmative", "custody_verify")
    graph.add_conditional_edges(
        "custody_verify",
        _route_after_custody,
        {"debate_affirmative": "debate_affirmative", "debate_adversarial": "debate_adversarial", "judge_audit": "judge_audit"},
    )
    graph.add_conditional_edges(
        "debate_adversarial",
        _route_after_adversarial,
        {"debate_affirmative": "debate_affirmative", "judge_audit": "judge_audit"},
    )
    graph.add_conditional_edges(
        "judge_audit", _route_after_judge, {"advance_claim": "advance_claim", "publish": "publish"}
    )
    graph.add_edge("publish", END)

    return graph.compile()
