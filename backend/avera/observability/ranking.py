"""Stable preliminary ordering, never a claim of validation confidence."""


def rank(hypotheses):
    ordered = sorted(hypotheses, key=lambda h: (
        -(len(set(h.evidence_for)) - len(set(h.evidence_against))), h.zone, h.claim, h.id))
    return [h.model_copy(update={"prior_rank": i}) for i, h in enumerate(ordered, 1)]
