"""Claim type taxonomy and the per-claim task record passed through the agent graph."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ClaimType = Literal["FACT_NUMERIC", "FACT_TREND", "CAUSE_ATTRIBUTION", "RATIO_IDENTITY"]


class ClaimTask(BaseModel):
    claim_id: str
    text: str
    claim_type: ClaimType
