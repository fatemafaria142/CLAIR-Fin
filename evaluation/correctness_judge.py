"""LLM-as-judge that grades a system answer against a gold answer as correct/partial/incorrect."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from clairfin.utils.llm import get_chat_llm, invoke_structured
from clairfin.utils.prompts import load_prompt

CorrectnessLabel = Literal["correct", "partial", "incorrect"]


class CorrectnessVerdict(BaseModel):
    label: CorrectnessLabel
    rationale: str


def grade_correctness(question: str, gold_answer: str, system_answer: str) -> CorrectnessVerdict:
    llm = get_chat_llm(temperature=0, max_tokens=300).with_structured_output(CorrectnessVerdict)
    return invoke_structured(
        llm,
        [
            ("system", load_prompt("tools/correctness_judge")),
            (
                "human",
                f"QUESTION: {question}\n\nGOLD ANSWER:\n{gold_answer}\n\nSYSTEM ANSWER:\n{system_answer}",
            ),
        ],
        default=CorrectnessVerdict(label="incorrect", rationale="correctness check did not complete (truncated output)"),
    )
