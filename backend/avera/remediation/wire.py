"""Converts internal remediation models into the shared contracts published on /stream.

Fields the shared contract has no slot for travel inside `Approval.payload` (a free-form dict) or are dropped.
Anything the contract cannot represent yet returns None and is held back from the stream.
"""

from __future__ import annotations

import logging

from avera import contracts as c
from avera.remediation.common import kobo_to_naira
from avera.remediation.models import (
    Approval,
    ApprovalKind,
    ApprovalLevel,
    ApprovalStatus,
    BlastRadius,
    CodePatch,
    ConfigChange,
    Estimate,
    FixPlan,
    ReconciliationBatch,
    ResolutionEvent,
    SmsDraft,
)

log = logging.getLogger(__name__)

SYSTEM_APPROVER = "avera-system"
_LEVELS = {ApprovalLevel.CUSTOMER_DISPUTE: c.ApprovalLevel.CUSTOMER_DISPUTE, ApprovalLevel.ENGINEER: c.ApprovalLevel.ENGINEER}


def describe_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{text} {rest} minutes" if rest else text


def _eta_text(estimate: Estimate, suffix: str = "") -> str:
    text = f"{describe_minutes(estimate.minutes)}{suffix}"
    return text if estimate.confidence == "high" else f"about {text} (low confidence)"


def fix_plan(plan: FixPlan) -> c.FixPlan:
    tracks = {s.track: s for s in plan.steps}
    breakdown = "; ".join(
        f"{describe_minutes(tracks[t].eta_minutes)} for {label}"
        for t, label in (("permanent", "permanent fix"), ("reconciliation", "reconciliation"))
        if t in tracks
    )
    rollback = (
        f"Restore {plan.rollback.action.to_route} routing if {plan.rollback.route} error rate exceeds "
        f"{plan.rollback.threshold:.0%} within {describe_minutes(plan.rollback.window_minutes)}."
        if plan.rollback else "No automatic rollback: containment is handled by the owning engineers."
    )
    verification = (
        f"Rerun the original failing Ghost Tests ({', '.join(plan.verification.rerun_hypothesis_ids)}) and confirm "
        f"live signals return to baseline: {', '.join(plan.verification.live_signals)}."
    )
    return c.FixPlan(
        id=plan.id,
        steps=[c.FixStep(title=s.title, description=s.detail, owner=s.owner)
               for s in sorted(plan.steps, key=lambda s: s.order) if s.track != "communication"],
        owners=plan.owners,
        rollback=rollback,
        verification=verification,
        eta_containment=_eta_text(plan.eta_containment, " after approval"),
        eta_full=_eta_text(plan.eta_full) + (f" ({breakdown})" if breakdown else ""),
    )


def blast_radius(br: BlastRadius) -> c.BlastRadius:
    return c.BlastRadius(
        id=br.id,
        txn_count=br.txn_count,
        customer_count=br.customer_count,
        silent_count=br.silent_count,
        exposure_ngn=kobo_to_naira(br.exposure_kobo),
        customers=[cust.customer_token for cust in br.customers],
        customers_complete=True,
    )


def _payload(approval: Approval) -> dict:
    p = approval.payload
    meta = {
        "incident_id": approval.incident_id,
        "payload_hash": approval.payload_hash,
        "required_role": str(approval.required_role),
        "requested_by": approval.requested_by,
        "requested_at": approval.requested_at.isoformat(),
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "supersedes": approval.supersedes,
    }
    if approval.status in (ApprovalStatus.SUPERSEDED, ApprovalStatus.EXPIRED):
        meta["closed_reason"] = str(approval.status)
    if approval.decision_reason:
        meta["decision_reason"] = approval.decision_reason

    if isinstance(p, SmsDraft):
        return {**meta, "action": "send_customer_sms", "message": p.messages[0].text if p.messages else "",
                "samples": [m.model_dump() for m in p.messages[:3]], "template": p.template,
                "recipient_count": p.recipient_count, "eta_phrase": p.eta_phrase, "fallback_used": p.fallback_used}
    if isinstance(p, ConfigChange):
        return {**meta, "action": "reroute", "plan_id": p.plan_id, "destination_bank": p.change.destination,
                "from_route": p.change.from_route, "to_route": p.change.to_route,
                "simulated_success_rate": p.simulation.success_rate, "simulation_samples": p.simulation.samples,
                "rollback": p.rollback.model_dump(mode="json") if p.rollback else None}
    if isinstance(p, CodePatch):
        return {**meta, "action": "review_connector_patch", "repository": p.repository, "target": p.target,
                "summary": p.summary, "diff": p.diff, "diff_url": f"/approvals/{approval.id}",
                "sandbox": p.sandbox.model_dump()}
    if isinstance(p, ReconciliationBatch):
        return {**meta, "action": "prepare_reconciliation_batch", "batch_id": p.id, "item_count": len(p.items),
                "total_ngn": str(kobo_to_naira(p.total_kobo)), "executed_by_avera": False}
    raise TypeError(f"unknown payload {type(p).__name__}")


def approval(item: Approval) -> c.Approval | None:
    try:
        kind = c.ApprovalKind(str(item.kind))
    except ValueError:
        log.warning("shared contract has no approval kind %r; %s held back from /stream", str(item.kind), item.id)
        return None

    if item.status in (ApprovalStatus.SUPERSEDED, ApprovalStatus.EXPIRED):
        status, approver = c.ApprovalStatus.REJECTED, SYSTEM_APPROVER
    else:
        status, approver = c.ApprovalStatus(str(item.status)), item.approver
    pending = status == c.ApprovalStatus.PENDING
    return c.Approval(
        id=item.id,
        level=_LEVELS[item.level],
        kind=kind,
        payload=_payload(item),
        status=status,
        approver=None if pending else approver,
        decided_at=None if pending else item.decided_at,
    )


def resolution_event(event: ResolutionEvent) -> c.ResolutionEvent:
    return c.ResolutionEvent(
        id=event.id,
        action=str(event.action),
        status=event.status,
        diff_url=event.diff_url,
        alert=event.alert.model_dump(mode="json") if event.alert else None,
        verified_by_ghost_test=event.verified_by_ghost_test,
    )


def supports_kind(kind: ApprovalKind) -> bool:
    return str(kind) in {k.value for k in c.ApprovalKind}
