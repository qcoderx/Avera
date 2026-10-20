"""Deterministic fix playbooks. Each turns a validated hypothesis into ordered, owned, approvable steps.

Playbooks are tried in order; the out-of-reach playbook always applies last, so every incident gets a plan.
Each playbook also names the fingerprint traits that scope the blast radius. Change markers such as
`app_version` are candidate causes, not impact scope, so they are never used to select affected customers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from avera.remediation.models import (
    ActionType,
    ApprovalKind,
    PlanStep,
    RollbackSpec,
    RouteChange,
    ValidatedHandoff,
)

ROLLBACK_ERROR_THRESHOLD = 0.02
ROLLBACK_WINDOW_MINUTES = 10
PATH_TRAITS = ("product", "route", "destination_bank", "error_code")


@dataclass(frozen=True)
class PlaybookDraft:
    name: str
    steps: list[PlanStep]
    rollback: RollbackSpec | None
    live_signals: list[str]
    known_fix: bool
    impact_traits: tuple[str, ...]
    containment_change: RouteChange | None = None
    notes: list[str] = field(default_factory=list)


class Playbook(Protocol):
    name: str

    def build(self, handoff: ValidatedHandoff) -> PlaybookDraft | None: ...


def _reconciliation_step(step_id: str, order: int) -> PlanStep:
    return PlanStep(
        id=step_id, order=order, track="reconciliation", action=ActionType.RECONCILIATION_PREP,
        title="Reconcile uncertain transfer states",
        detail="Re-query status for affected transactions and queue unconfirmed items for human reversal review. "
               "Avera prepares the batch only.",
        owner="reconciliation", approval_kind=ApprovalKind.RECONCILIATION, eta_minutes=240,
    )


def _communication_step(step_id: str, order: int) -> PlanStep:
    return PlanStep(
        id=step_id, order=order, track="communication", action=ActionType.NOTIFY_CUSTOMERS,
        title="Notify affected customers",
        detail="Apology SMS drafted by Avera, sent only after Customer Dispute team approval.",
        owner="customer_dispute", approval_kind=ApprovalKind.SMS, eta_minutes=5,
    )


class GatewayAckFailurePlaybook:
    """A gateway connector stops acknowledging one destination and a healthy alternative route exists."""

    name = "gateway_ack_failure"
    _zone = re.compile(r"^(?P<gateway>gateway_[a-z0-9]+)\.(?P<destination>[a-z0-9_]+?)_connector$")

    def build(self, handoff: ValidatedHandoff) -> PlaybookDraft | None:
        match = self._zone.match(handoff.hypothesis.zone)
        control = handoff.test_result.control
        if not match or control is None or control.reproduced != 0:
            return None
        failing_route = f"{match['gateway']}.{match['destination']}"
        if control.route == failing_route or not control.route.endswith(f".{match['destination']}"):
            return None

        destination = str(handoff.incident.fingerprint.get("destination_bank") or match["destination"].upper())
        gateway_label = match["gateway"].replace("_", " ").title()
        change = RouteChange(destination=destination, from_route=failing_route, to_route=control.route)
        patch_owner = handoff.hypothesis.routed_to[0] if handoff.hypothesis.routed_to else "payments_integration"
        steps = [
            PlanStep(
                id="S1", order=1, track="containment", action=ActionType.REROUTE,
                title=f"Contain by rerouting {destination} transfers to {control.route}",
                detail=f"Move {destination} transfers from {failing_route} to {control.route} after approval; "
                       f"monitor the route error rate for {ROLLBACK_WINDOW_MINUTES} minutes.",
                owner="payments_operations", approval_kind=ApprovalKind.CONFIG, eta_minutes=10, route_change=change,
            ),
            _reconciliation_step("S2", 2),
            PlanStep(
                id="S3", order=3, track="permanent", action=ActionType.PATCH,
                title=f"Prepare permanent {gateway_label} connector patch",
                detail=f"Correct {gateway_label} acknowledgement timeout and retry handling; tested in the sandbox "
                       "and submitted for engineer review.",
                owner=patch_owner, approval_kind=ApprovalKind.CODE, eta_minutes=180,
            ),
            _communication_step("S4", 4),
        ]
        return PlaybookDraft(
            name=self.name,
            steps=steps,
            rollback=RollbackSpec(
                destination=destination, route=control.route, threshold=ROLLBACK_ERROR_THRESHOLD,
                window_minutes=ROLLBACK_WINDOW_MINUTES, action=change.inverse(),
            ),
            live_signals=[f"transfer_success_rate:{destination}", f"ack_timeout_rate:{failing_route}"],
            known_fix=True,
            impact_traits=("route", "destination_bank", "error_code"),
            containment_change=change,
        )


class OutOfReachPlaybook:
    """The fix lives somewhere Avera cannot act: a partner bank, hardware, a vendor system."""

    name = "out_of_reach"

    def build(self, handoff: ValidatedHandoff) -> PlaybookDraft:
        owners = handoff.hypothesis.routed_to or ["incident_command"]
        steps = [
            PlanStep(
                id="S1", order=1, track="containment", action=ActionType.ALERT_ENGINEERS,
                title=f"Alert {', '.join(owners)} with the evidenced cause",
                detail="Includes cause, confidence, Ghost Test evidence, affected components and next step.",
                owner=owners[0], eta_minutes=30,
            ),
            _reconciliation_step("S2", 2),
            _communication_step("S3", 3),
        ]
        return PlaybookDraft(
            name=self.name,
            steps=steps,
            rollback=None,
            live_signals=[f"zone_health:{handoff.hypothesis.zone}"],
            known_fix=False,
            impact_traits=PATH_TRAITS,
        )


DEFAULT_PLAYBOOKS: tuple[Playbook, ...] = (GatewayAckFailurePlaybook(),)
FALLBACK_PLAYBOOK = OutOfReachPlaybook()


def select_playbook(handoff: ValidatedHandoff, playbooks: tuple[Playbook, ...] = DEFAULT_PLAYBOOKS) -> PlaybookDraft:
    for playbook in playbooks:
        draft = playbook.build(handoff)
        if draft is not None:
            return draft
    return FALLBACK_PLAYBOOK.build(handoff)
