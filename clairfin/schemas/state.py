"""LangGraph shared state schema threaded through every node of the CLAIR-Fin agent graph."""
from __future__ import annotations

from typing import TypedDict

from clairfin.ledger.graph import ClaimLedger
from clairfin.schemas.claim import ClaimTask
from clairfin.schemas.evidence import EvidenceItem


class GraphState(TypedDict, total=False):
    question: str
    ledger: ClaimLedger
    claim_tasks: list[ClaimTask]
    claim_index: int

    text_evidence: list[EvidenceItem]
    table_evidence: list[EvidenceItem]
    chart_evidence: list[EvidenceItem]

    escalate: bool
    agreement_score: float
    affirmative_brief: str | None
    adversarial_findings: list[dict]
    recommend_abstain: bool
    debate_round: int

    custody_broken: bool
    custody_repair_attempts: int
    custody_repair_note: str | None

    verdicts: list[dict]
    final_answer: str | None
    citations: list[str]
    abstained: bool
