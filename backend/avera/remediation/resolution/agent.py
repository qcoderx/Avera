"""Resolution Agent: carries out approved plan steps, verifies them with Ghost Tests, and escalates what it cannot fix."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from avera.contracts import ContractObjectType
from avera.remediation import wire
from avera.remediation.approvals import ApprovalService
from avera.remediation.audit import AuditLog
from avera.remediation.common import canonical_hash, kobo_to_naira
from avera.remediation.models import (
    ActionType,
    Approval,
    ApprovalStatus,
    CodePatch,
    ConfigChange,
    EngineerAlert,
    ReconciliationBatch,
    ResolutionAction,
    ResolutionEvent,
    SmsDraft,
)
from avera.remediation.ports import Ports
from avera.remediation.resolution.sms_dispatch import SmsDispatchReport, dispatch_sms
from avera.remediation.store import RemediationCase, RemediationStatus, RemediationStore

log = logging.getLogger(__name__)

ACTOR = "resolution_agent"


class ResolutionAgent:
    def __init__(self, ports: Ports, approvals: ApprovalService, audit: AuditLog, store: RemediationStore) -> None:
        self._ports = ports
        self._approvals = approvals
        self._audit = audit
        self._store = store
        approvals.subscribe(self.on_decision)

    # ------------------------------------------------------------ entry points

    def start(self, case: RemediationCase) -> None:
        """Called once a plan exists. Out-of-reach plans escalate immediately; everything else waits for approval."""
        containment = case.plan.step("containment")
        if containment is not None and containment.action is ActionType.ALERT_ENGINEERS:
            self._raise_alert(case, severity="critical", next_step=containment.title)
            case.status = RemediationStatus.ESCALATED

    def on_decision(self, approval: Approval) -> None:
        case = self._store.get(approval.incident_id)
        if case is None:
            log.warning("decision %s for unknown incident %s", approval.id, approval.incident_id)
            return
        expected_approval_id = case.approval_ids.get(str(approval.kind))
        plan_id = getattr(approval.payload, "plan_id", case.plan.id)
        if expected_approval_id != approval.id or plan_id != case.plan.id:
            self._audit.append(
                ts=self._ports.clock.now(), actor=ACTOR, action="stale_approval_ignored",
                incident_id=case.incident_id, subject_type="approval", subject_id=approval.id,
                payload=approval.payload_hash,
                detail={"active_plan_id": case.plan.id, "approval_plan_id": plan_id},
            )
            log.warning("ignoring stale approval %s for incident %s", approval.id, approval.incident_id)
            return
        if approval.id in case.executed_approvals:
            return
        if approval.status == ApprovalStatus.REJECTED:
            case.executed_approvals.add(approval.id)
            self._emit(case, ResolutionAction.APPROVAL_REJECTED, "rejected", approval_id=approval.id,
                       detail={"kind": str(approval.kind), "reason": approval.decision_reason})
            if isinstance(approval.payload, (ConfigChange, CodePatch)):
                self._raise_alert(case, severity="high",
                                  next_step=f"{approval.kind} change rejected: {approval.decision_reason}")
            return
        if approval.status != ApprovalStatus.APPROVED:
            return
        if canonical_hash(approval.payload) != approval.payload_hash:
            self._emit(case, ResolutionAction.ALERT_RAISED, "refused", approval_id=approval.id,
                       detail={"reason": "approved payload failed integrity check; refusing to execute"})
            return

        payload = approval.payload
        if isinstance(payload, SmsDraft):
            self._send_sms(case, approval, payload)
            return

        case.executed_approvals.add(approval.id)
        if isinstance(payload, ConfigChange):
            self._apply_route(case, approval, payload)
        elif isinstance(payload, ReconciliationBatch):
            self._emit(case, ResolutionAction.RECONCILIATION_HANDED_OFF, "handed_off", approval_id=approval.id,
                       detail={"batch_id": payload.id, "items": len(payload.items),
                               "total_ngn": str(kobo_to_naira(payload.total_kobo)), "executed_by": "reconciliation"})
        elif isinstance(payload, CodePatch):
            self._emit(case, ResolutionAction.PATCH_APPROVED, "approved_for_merge", approval_id=approval.id,
                       diff_url=f"/approvals/{approval.id}", detail={"target": payload.target})

    def retry_approved_sms(self, approval_id: str) -> SmsDispatchReport:
        """Retry only recipients whose send failed; the gateway must honor idempotency keys."""
        approval = self._approvals.get(approval_id)
        if approval.status != ApprovalStatus.APPROVED or not isinstance(approval.payload, SmsDraft):
            raise ValueError("only an approved SMS request can be retried")
        case = self._require(approval.incident_id)
        if case.approval_ids.get(str(approval.kind)) != approval.id:
            raise ValueError("SMS approval is not for the active remediation plan")
        if approval.id in case.executed_approvals:
            raise ValueError("SMS approval has no failed recipients to retry")
        if approval.id not in case.sms_retry_customers:
            raise ValueError("SMS approval has not had a failed delivery attempt")
        return self._send_sms(case, approval, approval.payload)

    def verify(self, incident_id: str) -> RemediationStatus:
        """Rerun the original failing Ghost Tests, then check live signals. Safe to call repeatedly."""
        case = self._require(incident_id)
        if case.status not in (RemediationStatus.CONTAINING, RemediationStatus.CONTAINED):
            return case.status

        hypothesis_ids = case.plan.verification.rerun_hypothesis_ids
        results = self._ports.ghost_tests.rerun(incident_id, hypothesis_ids)
        passed = bool(results) and all(r.rerun_passed for r in results)
        self._emit(case, ResolutionAction.VERIFIED, "verified_pass" if passed else "verified_fail",
                   verified_by_ghost_test=passed, detail={"test_results": [r.id for r in results]})
        if not passed:
            case.status = RemediationStatus.CONTAINING
            return case.status

        live = self._ports.metrics.live_signals_normal(incident_id, case.plan.verification.live_signals)
        if not live or not all(live.values()):
            case.status = RemediationStatus.CONTAINED
            return case.status

        case.status = RemediationStatus.RESOLVED
        open_follow_ups = [
            step.title for step in case.plan.steps
            if step.track in ("permanent", "reconciliation")
            and (str(step.approval_kind) not in case.approval_ids
                 or self._approvals.get(case.approval_ids[str(step.approval_kind)]).status != ApprovalStatus.APPROVED)
        ]
        self._emit(case, ResolutionAction.INCIDENT_RESOLVED, "resolved", verified_by_ghost_test=True,
                   detail={"live_signals": live, "open_follow_ups": open_follow_ups})
        return case.status

    def evaluate_rollback(self, incident_id: str) -> bool:
        """Poll during the rollback window. Rollback is pre-approved as part of the approved route change."""
        case = self._require(incident_id)
        rollback = case.plan.rollback
        if rollback is None or case.applied_change is None or case.applied_at is None:
            return False
        if case.status == RemediationStatus.ROLLED_BACK:
            return False
        if self._ports.clock.now() - case.applied_at > timedelta(minutes=rollback.window_minutes):
            return False

        metrics = self._ports.metrics.route_metrics(rollback.destination, rollback.route, rollback.window_minutes)
        if metrics.error_rate <= rollback.threshold:
            return False

        change_id = self._ports.routing.apply_route(rollback.action)
        case.status = RemediationStatus.ROLLED_BACK
        case.applied_change = rollback.action
        self._emit(case, ResolutionAction.ROUTE_ROLLED_BACK, "rolled_back",
                   approval_id=case.approval_ids.get("config"),
                   detail={"change_id": change_id, "error_rate": round(metrics.error_rate, 4),
                           "threshold": rollback.threshold, "restored": rollback.action.model_dump(mode="json")})
        self._raise_alert(case, severity="critical",
                          next_step=f"{rollback.route} error rate {metrics.error_rate:.1%} breached "
                                    f"{rollback.threshold:.0%}; routing restored, choose a new containment")
        return True

    # ------------------------------------------------------------ actions

    def _apply_route(self, case: RemediationCase, approval: Approval, payload: ConfigChange) -> None:
        change_id = self._ports.routing.apply_route(payload.change)
        case.applied_change = payload.change
        case.applied_at = self._ports.clock.now()
        case.status = RemediationStatus.CONTAINING
        self._emit(case, ResolutionAction.ROUTE_APPLIED, "applied", approval_id=approval.id,
                   detail={"change_id": change_id, **payload.change.model_dump(mode="json")})
        self.verify(case.incident_id)
        self._open_patch(case)

    def _open_patch(self, case: RemediationCase) -> None:
        patch_step = case.plan.step("permanent")
        if patch_step is None or patch_step.action is not ActionType.PATCH or "code" in case.approval_ids:
            return
        handoff = case.handoff
        proposal = self._ports.patch_author.write_patch(handoff.hypothesis, handoff.test_result)
        report = self._ports.sandbox.run_patch(proposal.repository, proposal.target, proposal.diff)
        if not report.passed:
            self._raise_alert(case, severity="high",
                              next_step=f"generated patch for {proposal.target} failed sandbox tests "
                                        f"({report.tests_failed}/{report.tests_run}); needs an engineer")
            return
        approval = self._approvals.request(
            payload=CodePatch(
                incident_id=case.incident_id, plan_id=case.plan.id, hypothesis_id=handoff.hypothesis.id,
                repository=proposal.repository, target=proposal.target, summary=proposal.summary,
                diff=proposal.diff, sandbox=report,
            ),
            subject_key=f"code:{case.incident_id}",
            requested_by=ACTOR,
        )
        case.approval_ids["code"] = approval.id
        self._emit(case, ResolutionAction.PATCH_OPENED, "awaiting_engineer_review", approval_id=approval.id,
                   diff_url=f"/approvals/{approval.id}", detail={"target": proposal.target})

    def _send_sms(self, case: RemediationCase, approval: Approval, draft: SmsDraft) -> SmsDispatchReport:
        retry_customers = case.sms_retry_customers.get(approval.id)
        report = dispatch_sms(draft, approval.id, self._ports.contacts, self._ports.sms,
                              customer_tokens=retry_customers)
        if report.failed:
            case.sms_retry_customers[approval.id] = set(report.failed_customer_tokens)
        else:
            case.sms_retry_customers.pop(approval.id, None)
            case.executed_approvals.add(approval.id)
        self._emit(case, ResolutionAction.SMS_SENT, "sent" if report.failed == 0 else "partially_failed",
                   approval_id=approval.id,
                   detail={"sent": report.sent, "skipped_opted_out": report.skipped_opted_out,
                           "skipped_unreachable": report.skipped_unreachable, "failed": report.failed})
        return report

    def _raise_alert(self, case: RemediationCase, *, severity: str, next_step: str) -> None:
        handoff = case.handoff
        rejected = [f"{r.hypothesis_id} {r.verdict} at {r.confidence:.0%}" for r in handoff.rivals]
        alert = EngineerAlert(
            incident_id=case.incident_id,
            hypothesis_id=handoff.hypothesis.id,
            severity=severity,
            cause=handoff.hypothesis.claim,
            confidence=handoff.test_result.confidence,
            evidence=[*handoff.hypothesis.evidence_for,
                      f"Ghost Test reproduced {handoff.test_result.reproduced} of {handoff.test_result.runs}",
                      *rejected],
            affected_components=[handoff.hypothesis.zone],
            recommended_next_step=next_step,
            routed_to=handoff.hypothesis.routed_to,
        )
        self._emit(case, ResolutionAction.ALERT_RAISED, "raised", alert=alert)

    # ------------------------------------------------------------ plumbing

    def _require(self, incident_id: str) -> RemediationCase:
        case = self._store.get(incident_id)
        if case is None:
            raise KeyError(f"no remediation case for incident {incident_id}")
        return case

    def _emit(self, case: RemediationCase, action: ResolutionAction, status: str, **fields: Any) -> ResolutionEvent:
        event = ResolutionEvent(
            id=f"resolution-{case.incident_id}-{case.event_seq + 1:03d}",
            incident_id=case.incident_id,
            ts=self._ports.clock.now(),
            action=action,
            status=status,
            **fields,
        )
        case.events.append(event)
        self._audit.append(ts=event.ts, actor=ACTOR, action=str(action), incident_id=case.incident_id,
                           subject_type="resolution_event", subject_id=event.id, payload=event,
                           detail={"status": status, "remediation_status": str(case.status)})
        self._ports.events.publish(ContractObjectType.RESOLUTION_EVENT, wire.resolution_event(event))
        return event
