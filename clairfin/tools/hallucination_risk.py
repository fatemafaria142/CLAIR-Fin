from __future__ import annotations

_WEIGHTS = {
    "entailment": 0.40,
    "authority": 0.25,
    "custody": 0.20,
    "attacks": 0.15,
}
_MAX_ATTACK_CONTRIBUTION = 3


def compute_hri(
    *,
    entailment_confidence: float,
    authority_score: float,
    custody_repairs: int,
    max_repairs: int,
    high_severity_attacks: int,
) -> float:
    entailment_risk = 1.0 - entailment_confidence
    authority_risk = 1.0 - min(authority_score, 1.0)
    custody_risk = custody_repairs / max(max_repairs, 1)
    attack_risk = min(high_severity_attacks / _MAX_ATTACK_CONTRIBUTION, 1.0)

    hri = (
        _WEIGHTS["entailment"] * entailment_risk
        + _WEIGHTS["authority"] * authority_risk
        + _WEIGHTS["custody"] * custody_risk
        + _WEIGHTS["attacks"] * attack_risk
    )
    return round(min(max(hri, 0.0), 1.0), 3)
