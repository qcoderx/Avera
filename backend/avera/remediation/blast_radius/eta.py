"""Time-to-fix estimates from the plan's critical path.

Tracks run in parallel unless a step declares depends_on. Customer communication is not part of the fix time.
When incident memory exists, historical durations for the same fingerprint should replace step defaults.
"""

from __future__ import annotations

from avera.remediation.models import Estimate, PlanStep

FIX_TRACKS = {"containment", "permanent", "reconciliation"}


def critical_path_minutes(steps: list[PlanStep]) -> int:
    by_id = {s.id: s for s in steps}
    finish: dict[str, int] = {}

    def earliest_finish(step: PlanStep, visiting: frozenset[str] = frozenset()) -> int:
        if step.id in finish:
            return finish[step.id]
        if step.id in visiting:
            raise ValueError(f"dependency cycle at {step.id}")
        start = max(
            (earliest_finish(by_id[d], visiting | {step.id}) for d in step.depends_on if d in by_id),
            default=0,
        )
        finish[step.id] = start + step.eta_minutes
        return finish[step.id]

    return max((earliest_finish(s) for s in steps), default=0)


def estimate(steps: list[PlanStep], *, high_confidence: bool) -> tuple[Estimate, Estimate]:
    confidence = "high" if high_confidence else "low"
    basis = "playbook step durations (no incident memory yet)"
    containment_steps = [s for s in steps if s.track == "containment"]
    fix_steps = [s for s in steps if s.track in FIX_TRACKS]
    return (
        Estimate(minutes=critical_path_minutes(containment_steps), confidence=confidence, basis=basis),
        Estimate(minutes=critical_path_minutes(fix_steps), confidence=confidence, basis=basis),
    )
