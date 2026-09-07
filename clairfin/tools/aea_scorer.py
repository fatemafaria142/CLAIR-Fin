"""Asymmetric Evidence Authority scoring: weighted vs. uniform modality voting and coverage scoring for claim verdicts."""
from __future__ import annotations

from functools import lru_cache

from clairfin.schemas.aea import AEAWeights
from configs.loaders import load_yaml
from configs.settings import get_settings


@lru_cache
def load_aea_weights() -> AEAWeights:
    settings = get_settings()
    return AEAWeights.model_validate(load_yaml(settings.aea_weights_path))


def score_claim(claim_type: str, modality_confidences: dict[str, float]) -> tuple[str | None, float]:
    """Returns (winning_modality, authority_weighted_score). `modality_confidences` maps modality
    -> retrieval/extraction confidence in [0, 1]; modalities absent from the AEA weight table for
    this claim type (e.g. `chart` for a `CAUSE_ATTRIBUTION` claim) are ignored even if present."""
    if not modality_confidences:
        return None, 0.0
    weights = load_aea_weights().weights_for(claim_type)
    scored = {
        modality: weights.get(modality, 0.0) * confidence
        for modality, confidence in modality_confidences.items()
        if modality in weights
    }
    if not scored:
        return None, 0.0
    winner = max(scored, key=scored.get)
    return winner, scored[winner]


def score_claim_uniform(claim_type: str, modality_confidences: dict[str, float]) -> tuple[str | None, float]:
    """Counterfactual used by the Authority Docket (`clairfin/tools/authority_docket.py`): what
    would win under equal-weight voting across the same modalities, instead of AEA's asserted
    trust prior. Comparing this against `score_claim()`'s actual pick, for every decision, is what
    makes the prior's real effect auditable rather than only declared in `aea_weights.yaml`."""
    if not modality_confidences:
        return None, 0.0
    weights = load_aea_weights().weights_for(claim_type)
    active_modalities = [m for m in modality_confidences if m in weights]
    if not active_modalities:
        return None, 0.0
    uniform_weight = 1.0 / len(active_modalities)
    scored = {m: uniform_weight * modality_confidences[m] for m in active_modalities}
    winner = max(scored, key=scored.get)
    return winner, scored[winner]


def coverage_score(claim_type: str, modalities_present: set[str]) -> float:
    """Sum of AEA weight for every modality that produced *some* evidence, regardless of how
    confident it was. Used by the Ledger Guardian as an "agreement" signal: if the
    highest-authority modality for this claim type never weighed in, coverage is low and the
    claim should escalate to debate rather than fast-path (docs/planning.md §7.2)."""
    weights = load_aea_weights().weights_for(claim_type)
    return sum(weight for modality, weight in weights.items() if modality in modalities_present)
