from __future__ import annotations

from collections import Counter

from clairfin.ledger.graph import ClaimLedger


def summarize(ledger: ClaimLedger) -> dict:
    """Per-run summary: how often AEA's asymmetric weighting actually changed the outcome versus
    equal-weight voting, broken down by claim type and by which modality benefited."""
    docket = ledger.authority_docket
    if not docket:
        return {"decisions": 0, "changed_outcome": 0, "changed_outcome_rate": 0.0, "by_claim_type": {}}

    changed = [d for d in docket if d.asymmetry_changed_outcome]
    by_claim_type: dict[str, dict[str, int]] = {}
    for decision in docket:
        bucket = by_claim_type.setdefault(decision.claim_type, {"decisions": 0, "changed_outcome": 0})
        bucket["decisions"] += 1
        if decision.asymmetry_changed_outcome:
            bucket["changed_outcome"] += 1

    beneficiary_counts = Counter(d.asymmetric_winner for d in changed if d.asymmetric_winner)

    return {
        "decisions": len(docket),
        "changed_outcome": len(changed),
        "changed_outcome_rate": round(len(changed) / len(docket), 3),
        "by_claim_type": by_claim_type,
        "modalities_favored_by_asymmetry": dict(beneficiary_counts),
    }
