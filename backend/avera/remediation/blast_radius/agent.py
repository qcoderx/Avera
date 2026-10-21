"""Blast Radius Agent: validated hypothesis in; fix plan, time estimate, affected customers and approval requests out."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo

from avera.contracts import ContractObjectType, Incident, TestVerdict
from avera.remediation import wire
from avera.remediation.approvals import ApprovalService
from avera.remediation.audit import AuditLog
from avera.remediation.blast_radius.eta import estimate
from avera.remediation.blast_radius.impact import compute_blast_radius
from avera.remediation.blast_radius.playbooks import FALLBACK_PLAYBOOK, PlaybookDraft, select_playbook
from avera.remediation.blast_radius.sms import draft_sms
from avera.remediation.common import BANK_TZ
from avera.remediation.models import (
    ConfigChange,
    FixPlan,
    ReconciliationBatch,
    ReconciliationItem,
    SimulationResult,
    TransactionRecord,
    ValidatedHandoff,
    VerificationSpec,
)
from avera.remediation.ports import Ports
from avera.remediation.store import RemediationCase, RemediationStore

ACTOR = "blast_radius_agent"


class HandoffRejected(ValueError):
    pass


@dataclass(frozen=True)
class BlastRadiusConfig:
    confidence_threshold: float = 0.85
    min_simulated_success: float = 0.95
    display_tz: tzinfo = field(default=BANK_TZ)


def incident_window_start(incident: Incident) -> datetime:
    first_seen = incident.fingerprint.get("first_seen")
    return datetime.fromisoformat(str(first_seen)) if first_seen else incident.opened_at


class BlastRadiusAgent:
    def __init__(
        self,
        ports: Ports,
        approvals: ApprovalService,
        audit: AuditLog,
        store: RemediationStore,
        config: BlastRadiusConfig = BlastRadiusConfig(),
    ) -> None:
        self._ports = ports
        self._approvals = approvals
        self._audit = audit
        self._store = store
        self._config = config

    def handle(self, handoff: ValidatedHandoff) -> RemediationCase:
        self._check_handoff(handoff)
        incident = handoff.incident
        existing = self._store.get(incident.id)
        if existing and existing.plan.hypothesis_id == handoff.hypothesis.id:
            return existing

        now = self._ports.clock.now()
        draft = select_playbook(handoff)
        missing_scope = sorted(set(draft.impact_traits) - set(handoff.incident.fingerprint))
        if missing_scope:
            raise HandoffRejected(
                "incident fingerprint is missing impact-scope traits: " + ", ".join(missing_scope)
            )
        draft, simulation = self._simulate_containment(handoff, draft)
        eta_containment, eta_full = estimate(
            draft.steps, high_confidence=draft.known_fix and (simulation is None or simulation.passed)
        )

        version = existing.plan.version + 1 if existing else 1
        suffix = "" if version == 1 else f"-v{version}"
        plan = FixPlan(
            id=f"plan-{incident.id}{suffix}",
            incident_id=incident.id,
            hypothesis_id=handoff.hypothesis.id,
            version=version,
            playbook=draft.name,
            cause=handoff.hypothesis.claim,
            cause_confidence=handoff.test_result.confidence,
            impact_traits=list(draft.impact_traits),
            steps=draft.steps,
            owners=list(dict.fromkeys(s.owner for s in sorted(draft.steps, key=lambda s: s.order))),
            rollback=draft.rollback,
            verification=VerificationSpec(rerun_hypothesis_ids=[handoff.hypothesis.id], live_signals=draft.live_signals),
            eta_containment=eta_containment,
            eta_full=eta_full,
            simulation=simulation,
            created_at=now,
        )

        traits = {k: str(incident.fingerprint[k]) for k in draft.impact_traits if k in incident.fingerprint}
        window_start = incident_window_start(incident)
        bank = self._ports.bank
        transactions = bank.find_transactions(traits, window_start, now)
        blast_radius = compute_blast_radius(
            blast_id=f"blast-{incident.id}{suffix}",
            incident_id=incident.id,
            traits=traits,
            window_start=window_start,
            transactions=transactions,
            complaints=bank.complaints_between(window_start, now),
            signals=bank.behaviour_signals_between(window_start, now),
            as_of=now,
            projection_horizon_minutes=eta_containment.minutes,
        )

        self._record("fix_plan_created", incident.id, "fix_plan", plan.id, plan,
                     {"playbook": plan.playbook, "notes": draft.notes})
        self._record("blast_radius_computed", incident.id, "blast_radius", blast_radius.id, blast_radius,
                     {"matched_on": traits, "customers": blast_radius.customer_count})
        self._ports.events.publish(ContractObjectType.FIX_PLAN, wire.fix_plan(plan))
        self._ports.events.publish(ContractObjectType.BLAST_RADIUS, wire.blast_radius(blast_radius))

        case = RemediationCase(handoff=handoff, plan=plan, blast_radius=blast_radius)
        self._store.save(case)
        self._request_approvals(case, draft, simulation, transactions)
        return case

    def _check_handoff(self, handoff: ValidatedHandoff) -> None:
        result = handoff.test_result
        if result.hypothesis_id != handoff.hypothesis.id:
            raise HandoffRejected("test result does not belong to the handed-off hypothesis")
        if result.verdict != TestVerdict.VALIDATED or result.confidence < self._config.confidence_threshold:
            raise HandoffRejected(
                f"{result.hypothesis_id} is {result.verdict} at {result.confidence:.0%}; "
                f"threshold is {self._config.confidence_threshold:.0%}"
            )

    def _simulate_containment(
        self, handoff: ValidatedHandoff, draft: PlaybookDraft
    ) -> tuple[PlaybookDraft, SimulationResult | None]:
        if draft.containment_change is None:
            return draft, None
        simulation = self._ports.twin.simulate_route(draft.containment_change)
        if simulation.passed and simulation.success_rate >= self._config.min_simulated_success:
            return draft, simulation
        fallback = FALLBACK_PLAYBOOK.build(handoff)
        note = (
            f"containment {draft.containment_change.from_route} -> {draft.containment_change.to_route} "
            f"failed simulation at {simulation.success_rate:.1%}"
        )
        return replace(fallback, notes=[*fallback.notes, note]), simulation

    def _request_approvals(
        self,
        case: RemediationCase,
        draft: PlaybookDraft,
        simulation: SimulationResult | None,
        transactions: list[TransactionRecord],
    ) -> None:
        plan, blast_radius = case.plan, case.blast_radius
        incident_id = plan.incident_id

        # A code proposal is tied to the plan that produced it. Retire an older pending
        # proposal before this plan can receive new decisions.
        self._approvals.withdraw(incident_id=incident_id, subject_key=f"code:{incident_id}", requested_by=ACTOR)

        if draft.containment_change is not None and simulation is not None:
            config = ConfigChange(incident_id=incident_id, plan_id=plan.id, change=draft.containment_change,
                                  rollback=plan.rollback, simulation=simulation)
            case.approval_ids["config"] = self._approvals.request(
                payload=config, subject_key=f"config:{incident_id}", requested_by=ACTOR
            ).id
        else:
            self._approvals.withdraw(incident_id=incident_id, subject_key=f"config:{incident_id}", requested_by=ACTOR)

        affected_ids = {txn_id for c in blast_radius.customers for txn_id in c.txn_ids}
        details = {t.txn_id: (t.amount_kobo, t.ts) for t in transactions if t.txn_id in affected_ids}
        sms = draft_sms(blast_radius, plan.eta_full, details, reference=incident_id, tz=self._config.display_tz)
        case.approval_ids["sms"] = self._approvals.request(
            payload=sms, subject_key=f"sms:{incident_id}", requested_by=ACTOR
        ).id

        items = [
            ReconciliationItem(txn_id=txn_id, customer_token=c.customer_token, amount_kobo=details[txn_id][0])
            for c in blast_radius.customers
            for txn_id in c.txn_ids
        ]
        batch = ReconciliationBatch(id=f"recon-{incident_id}-v{plan.version}", incident_id=incident_id,
                                    items=items, total_kobo=sum(i.amount_kobo for i in items))
        case.approval_ids["reconciliation"] = self._approvals.request(
            payload=batch, subject_key=f"reconciliation:{incident_id}", requested_by=ACTOR
        ).id

    def _record(self, action: str, incident_id: str, subject_type: str, subject_id: str, payload, detail=None) -> None:
        self._audit.append(ts=self._ports.clock.now(), actor=ACTOR, action=action, incident_id=incident_id,
                           subject_type=subject_type, subject_id=subject_id, payload=payload, detail=detail)
