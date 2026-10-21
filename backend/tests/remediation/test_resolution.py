from __future__ import annotations

from dataclasses import replace

import pytest

from avera.contracts import ContractObjectType, ResolutionEvent as SharedResolutionEvent
from avera.remediation.fixtures import build_demo_world
from avera.remediation.models import ApprovalStatus, ResolutionAction
from avera.remediation.pipeline import build_remediation
from avera.remediation.store import RemediationStatus
from tests.remediation.conftest import DISPUTE, ENGINEER, approve


def actions(case) -> list[ResolutionAction]:
    return [e.action for e in case.events]


def event(case, action: ResolutionAction):
    return next(e for e in case.events if e.action == action)


def test_nothing_happens_before_approval(case, world):
    assert world.sms.outbox == {}
    assert world.bank.applied == []
    assert case.status == RemediationStatus.AWAITING_APPROVAL


def test_full_demo_loop(case, remediation, world):
    approve(remediation, case.approval_ids["sms"], DISPUTE)
    approve(remediation, case.approval_ids["reconciliation"], DISPUTE)
    world.clock.advance(minutes=6)
    approve(remediation, case.approval_ids["config"], ENGINEER)

    assert world.bank.routes["BANK_X"] == "gateway_c.bank_x"
    assert case.status == RemediationStatus.RESOLVED
    patch = remediation.approvals.get(case.approval_ids["code"])
    assert patch.status == ApprovalStatus.PENDING
    assert patch.payload.sandbox.passed and "+++ b/" in patch.payload.diff

    for expected in [ResolutionAction.SMS_SENT, ResolutionAction.RECONCILIATION_HANDED_OFF,
                     ResolutionAction.ROUTE_APPLIED, ResolutionAction.VERIFIED, ResolutionAction.INCIDENT_RESOLVED,
                     ResolutionAction.PATCH_OPENED]:
        assert expected in actions(case)
    assert event(case, ResolutionAction.VERIFIED).verified_by_ghost_test is True
    assert event(case, ResolutionAction.INCIDENT_RESOLVED).detail["open_follow_ups"] == [
        "Prepare permanent Gateway B connector patch"]

    approve(remediation, case.approval_ids["code"], ENGINEER)
    assert ResolutionAction.PATCH_APPROVED in actions(case)
    assert remediation.audit.verify()


def test_stream_matches_the_recorded_fixture_vocabulary(case, remediation, world):
    approve(remediation, case.approval_ids["config"], ENGINEER)
    streamed = [SharedResolutionEvent.model_validate(e) for e in world.events.of_type(ContractObjectType.RESOLUTION_EVENT)]
    pairs = [(e.action, e.status) for e in streamed]
    assert ("apply_approved_reroute", "applied") in pairs
    assert ("verify_after_approved_reroute", "verified_pass") in pairs
    assert ("open_connector_patch_for_review", "awaiting_engineer_review") in pairs
    patch_event = next(e for e in streamed if e.action == "open_connector_patch_for_review")
    assert patch_event.diff_url == f"/approvals/{case.approval_ids['code']}"


def test_sms_respects_contact_preferences_and_is_idempotent(case, remediation, world):
    approve(remediation, case.approval_ids["sms"], DISPUTE)
    sent = event(case, ResolutionAction.SMS_SENT).detail
    assert sent["sent"] == 1109 - len(world.opted_out)
    assert sent["skipped_opted_out"] == len(world.opted_out)
    assert len(world.sms.outbox) == sent["sent"]

    remediation.resolution.on_decision(remediation.approvals.get(case.approval_ids["sms"]))
    assert len(world.sms.outbox) == sent["sent"]


def test_failed_sms_recipient_can_be_retried_without_resending_successes():
    world = build_demo_world()
    delegate = world.sms
    failed_token = next(
        f"cust_demo_{index:04d}" for index in range(1, 1110)
        if world.ports.contacts.resolve(f"cust_demo_{index:04d}").sms_opt_in
    )

    class FailOnceGateway:
        def __init__(self, delegate):
            self.delegate = delegate
            self.failed_once = False
            self.calls_by_token = {}

        def send(self, to, text, idempotency_key):
            token = idempotency_key.rsplit(":", 1)[-1]
            self.calls_by_token[token] = self.calls_by_token.get(token, 0) + 1
            if token == failed_token and not self.failed_once:
                self.failed_once = True
                raise TimeoutError("temporary SMS provider failure")
            return self.delegate.send(to, text, idempotency_key)

    gateway = FailOnceGateway(delegate)
    remediation = build_remediation(replace(world.ports, sms=gateway))
    case = remediation.on_validated(world.handoff)
    approval = remediation.approvals.get(case.approval_ids["sms"])

    remediation.approvals.decide(approval.id, decision="approve", approver=DISPUTE,
                                 payload_hash=approval.payload_hash)
    first = event(case, ResolutionAction.SMS_SENT)
    assert first.detail["failed"] == 1
    assert approval.id not in case.executed_approvals
    assert len(delegate.outbox) == len(approval.payload.messages) - len(world.opted_out) - 1

    retry = remediation.resolution.retry_approved_sms(approval.id)
    assert retry.failed == 0
    assert retry.sent == 1
    assert gateway.calls_by_token[failed_token] == 2
    assert len(delegate.outbox) == len(approval.payload.messages) - len(world.opted_out)
    assert approval.id in case.executed_approvals
    with pytest.raises(ValueError, match="no failed recipients"):
        remediation.resolution.retry_approved_sms(approval.id)


def test_sent_text_is_exactly_the_approved_text(case, remediation, world):
    approval = approve(remediation, case.approval_ids["sms"], DISPUTE)
    approved = {m.text for m in approval.payload.messages}
    assert {text for _, text in world.sms.outbox.values()} <= approved


def test_rejected_reroute_escalates_without_touching_production(case, remediation, world):
    config = remediation.approvals.get(case.approval_ids["config"])
    remediation.approvals.decide(config.id, decision="reject", approver=ENGINEER,
                                 payload_hash=config.payload_hash, reason="Gateway C is under maintenance")
    assert world.bank.applied == []
    assert {ResolutionAction.APPROVAL_REJECTED, ResolutionAction.ALERT_RAISED} <= set(actions(case))


def test_rollback_fires_when_new_route_degrades(case, remediation, world):
    approve(remediation, case.approval_ids["config"], ENGINEER)
    world.bank.route_error_rates[("BANK_X", "gateway_c.bank_x")] = 0.05
    world.clock.advance(minutes=4)
    assert remediation.resolution.evaluate_rollback(case.incident_id) is True
    assert world.bank.routes["BANK_X"] == "gateway_b.bank_x"
    assert case.status == RemediationStatus.ROLLED_BACK
    assert remediation.resolution.evaluate_rollback(case.incident_id) is False


def test_rollback_watch_ends_after_its_window(case, remediation, world):
    approve(remediation, case.approval_ids["config"], ENGINEER)
    world.bank.route_error_rates[("BANK_X", "gateway_c.bank_x")] = 0.05
    world.clock.advance(minutes=11)
    assert remediation.resolution.evaluate_rollback(case.incident_id) is False
    assert world.bank.routes["BANK_X"] == "gateway_c.bank_x"


def test_incident_stays_open_until_live_signals_recover(case, remediation, world):
    original = world.bank.live_signals_normal
    world.bank.live_signals_normal = lambda incident_id, signals: {s: False for s in signals}
    approve(remediation, case.approval_ids["config"], ENGINEER)
    assert case.status == RemediationStatus.CONTAINED

    world.bank.live_signals_normal = original
    assert remediation.resolution.verify(case.incident_id) == RemediationStatus.RESOLVED
