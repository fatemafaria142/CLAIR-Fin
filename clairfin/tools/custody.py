from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from clairfin.tools.entailment import check_entailment
from configs.loaders import load_yaml
from configs.settings import get_settings

CustodyVerdict = Literal["valid", "repaired", "broken"]


class CustodyEvent(BaseModel):
    claim_id: str
    stage: str
    verdict: CustodyVerdict
    detail: str


def _custody_config() -> dict:
    return load_yaml(get_settings().agent_budgets_path)["custody"]


def verify_grounding(evidence_text: str, drafted_text: str) -> tuple[bool, str]:
    """Is `drafted_text` actually supported by `evidence_text`? Reuses the same entailment
    mechanism as the terminal audit gate (`clairfin/tools/entailment.py`) — custody verification
    isn't a separate, weaker check bolted on for show; it's the same faithfulness standard,
    applied earlier, where a problem is cheaper to catch and repair."""
    if not drafted_text.strip():
        return False, "empty draft"
    threshold = _custody_config()["grounding_entailment_threshold"]
    verdict = check_entailment(premise=evidence_text, hypothesis=drafted_text)
    passed = verdict.label == "entails" and verdict.confidence >= threshold
    return passed, verdict.rationale


def max_repair_attempts() -> int:
    return _custody_config()["max_repair_attempts"]
