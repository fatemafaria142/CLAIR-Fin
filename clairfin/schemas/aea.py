"""Pydantic models for Asymmetric Evidence Authority (AEA): per-claim-type modality weights and the Authority Docket's asymmetric-vs-uniform decision record."""
from __future__ import annotations

from pydantic import BaseModel, computed_field


class AEAWeights(BaseModel):
    claim_types: dict[str, dict[str, float]]

    def weights_for(self, claim_type: str) -> dict[str, float]:
        return self.claim_types[claim_type]


class AuthorityDecision(BaseModel):
    """One entry in the Authority Docket (`clairfin/tools/authority_docket.py`): what AEA decided
    for one claim, and what equal-weight voting over the same evidence would have decided instead
    — the asserted trust prior's effect made visible per-decision, not just declared in YAML."""

    claim_id: str
    claim_type: str
    modality_confidences: dict[str, float]
    asymmetric_winner: str | None
    uniform_winner: str | None

    @computed_field
    @property
    def asymmetry_changed_outcome(self) -> bool:
        return self.asymmetric_winner != self.uniform_winner
