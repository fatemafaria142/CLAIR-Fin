from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt
from configs.loaders import load_yaml
from configs.settings import get_settings


class EntailmentVerdict(BaseModel):
    label: Literal["entails", "neutral", "contradicts"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


def check_entailment(premise: str, hypothesis: str) -> EntailmentVerdict:
    budgets = load_yaml(get_settings().agent_budgets_path)
    llm = get_chat_llm(temperature=0, max_tokens=budgets["entailment"]["max_output_tokens"]).with_structured_output(
        EntailmentVerdict
    )
    result = invoke_structured(
        llm,
        [
            ("system", load_prompt("tools/entailment_judge")),
            ("human", f"PREMISE:\n{premise}\n\nHYPOTHESIS:\n{hypothesis}"),
        ],
        default=EntailmentVerdict(label="neutral", confidence=0.0, rationale="entailment check did not complete (truncated output)"),
    )
    assert isinstance(result, EntailmentVerdict)
    return result
