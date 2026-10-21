from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from avera.remediation.approvals import ApprovalError, ApprovalNotPending, Approver, ApproverNotAuthorised, StalePayload
from avera.remediation.approvals.api import build_router
from avera.remediation.models import ApprovalLevel, ApprovalStatus, Role
from tests.remediation.conftest import DISPUTE, ENGINEER, approve


def test_levels_and_roles_follow_policy(case, remediation):
    sms = remediation.approvals.get(case.approval_ids["sms"])
    config = remediation.approvals.get(case.approval_ids["config"])
    recon = remediation.approvals.get(case.approval_ids["reconciliation"])
    assert (sms.level, sms.required_role) == (ApprovalLevel.CUSTOMER_DISPUTE, Role.CUSTOMER_DISPUTE)
    assert (recon.level, recon.required_role) == (ApprovalLevel.CUSTOMER_DISPUTE, Role.CUSTOMER_DISPUTE)
    assert (config.level, config.required_role) == (ApprovalLevel.ENGINEER, Role.ENGINEER)


def test_wrong_role_cannot_approve(case, remediation):
    with pytest.raises(ApproverNotAuthorised):
        approve(remediation, case.approval_ids["config"], DISPUTE)
    with pytest.raises(ApproverNotAuthorised):
        approve(remediation, case.approval_ids["sms"], ENGINEER)


def test_agent_cannot_approve_its_own_request(case, remediation):
    with pytest.raises(ApproverNotAuthorised):
        approve(remediation, case.approval_ids["sms"], Approver(id="blast_radius_agent", role=Role.CUSTOMER_DISPUTE))


def test_approval_is_bound_to_the_payload_that_was_shown(case, remediation):
    with pytest.raises(StalePayload):
        remediation.approvals.decide(case.approval_ids["sms"], decision="approve", approver=DISPUTE,
                                     payload_hash="0" * 64)


def test_decisions_are_final(case, remediation):
    approve(remediation, case.approval_ids["sms"], DISPUTE)
    with pytest.raises(ApprovalNotPending):
        approve(remediation, case.approval_ids["sms"], DISPUTE)


def test_rejection_requires_a_reason(case, remediation):
    approval = remediation.approvals.get(case.approval_ids["sms"])
    with pytest.raises(ApprovalError, match="reason"):
        remediation.approvals.decide(approval.id, decision="reject", approver=DISPUTE,
                                     payload_hash=approval.payload_hash)


def test_expired_approvals_cannot_be_decided(case, remediation, world):
    world.clock.advance(hours=5)
    with pytest.raises(ApprovalNotPending):
        approve(remediation, case.approval_ids["sms"], DISPUTE)
    assert remediation.approvals.get(case.approval_ids["sms"]).status == ApprovalStatus.EXPIRED


def test_new_plan_supersedes_pending_requests(case, remediation, world):
    old_sms = case.approval_ids["sms"]
    h_new = world.handoff.hypothesis.model_copy(update={"id": "H1b"})
    handoff = world.handoff.model_copy(update={
        "hypothesis": h_new,
        "test_result": world.handoff.test_result.model_copy(update={"hypothesis_id": "H1b"}),
    })
    new_case = remediation.on_validated(handoff)
    assert new_case.plan.version == 2
    assert remediation.approvals.get(old_sms).status == ApprovalStatus.SUPERSEDED
    assert remediation.approvals.get(new_case.approval_ids["sms"]).supersedes == old_sms


def test_new_plan_supersedes_pending_code_patch_and_listener_rejects_stale_approval(case, remediation, world):
    approve(remediation, case.approval_ids["config"], ENGINEER)
    old_code = remediation.approvals.get(case.approval_ids["code"])

    h_new = world.handoff.hypothesis.model_copy(update={"id": "H1-new"})
    handoff = world.handoff.model_copy(update={
        "hypothesis": h_new,
        "test_result": world.handoff.test_result.model_copy(update={"hypothesis_id": "H1-new"}),
    })
    new_case = remediation.on_validated(handoff)

    assert old_code.status == ApprovalStatus.PENDING
    assert remediation.approvals.get(old_code.id).status == ApprovalStatus.SUPERSEDED
    with pytest.raises(ApprovalNotPending):
        approve(remediation, old_code.id, ENGINEER)

    stale_approved = old_code.model_copy(update={"status": ApprovalStatus.APPROVED, "approver": ENGINEER.id})
    remediation.resolution.on_decision(stale_approved)
    assert new_case.plan.id != old_code.payload.plan_id
    assert all(e.action.value != "approve_connector_patch" for e in new_case.events)


def test_http_router_maps_errors(case, remediation):
    app = FastAPI()
    principal = {"value": ENGINEER}

    def current_approver():
        return principal["value"]

    app.include_router(build_router(remediation.approvals, current_approver=current_approver))
    client = TestClient(app)
    approval_id = case.approval_ids["config"]
    payload_hash = remediation.approvals.get(approval_id).payload_hash

    def post(body, headers=None):
        return client.post(f"/approvals/{approval_id}", json=body, headers=headers or {})

    principal["value"] = DISPUTE
    assert post({"decision": "approve", "payload_hash": payload_hash}).status_code == 403
    assert client.get(f"/approvals/{approval_id}").status_code == 404
    assert all(a["kind"] != "config" for a in client.get("/approvals", params={"status": "pending"}).json())

    principal["value"] = ENGINEER
    assert post({"decision": "approve", "payload_hash": "bad"}).status_code == 412
    # These untrusted headers cannot override the verified principal supplied by the dependency.
    assert post({"decision": "approve", "payload_hash": payload_hash},
                headers={"X-Avera-User": "attacker", "X-Avera-Role": "customer_dispute"}).status_code == 200
    assert post({"decision": "approve", "payload_hash": payload_hash}).status_code == 409
    assert client.get("/approvals/missing").status_code == 404


def test_audit_chain_records_everything_and_detects_tampering(case, remediation):
    approve(remediation, case.approval_ids["sms"], DISPUTE)
    entries = remediation.audit.entries(case.incident_id)
    actions = [e.action for e in entries]
    assert {"fix_plan_created", "blast_radius_computed", "approval_requested", "approval_approved",
            "send_approved_sms"} <= set(actions)
    assert remediation.audit.verify()

    log = remediation.audit
    log._entries[2] = log._entries[2].model_copy(update={"actor": "someone_else"})
    assert not log.verify()


def test_streamed_approvals_conform_and_carry_the_hash_to_send_back(case, remediation, world):
    from avera.contracts import Approval as SharedApproval, ContractObjectType

    streamed = {a["id"]: SharedApproval.model_validate(a) for a in world.events.of_type(ContractObjectType.APPROVAL)}
    assert set(streamed) == {"approval-config-AV-48291", "approval-sms-AV-48291",
                             "approval-reconciliation-AV-48291"}
    config = streamed["approval-config-AV-48291"]
    assert (config.level.value, config.payload["from_route"], config.payload["to_route"]) == (
        "level_3", "gateway_b.bank_x", "gateway_c.bank_x")
    approve_with = config.payload["payload_hash"]
    remediation.approvals.decide(config.id, decision="approve", approver=ENGINEER, payload_hash=approve_with)
    assert remediation.approvals.get(config.id).status == ApprovalStatus.APPROVED


def test_reconciliation_approval_is_published_using_the_shared_contract(case, world):
    from avera.contracts import ContractObjectType

    assert "reconciliation" in case.approval_ids
    assert any(a["kind"] == "reconciliation" for a in world.events.of_type(ContractObjectType.APPROVAL))


def test_ttl_is_configurable(world):
    from avera.remediation.approvals import ApprovalService, InMemoryApprovalRepository
    from avera.remediation.audit import InMemoryAuditLog
    from avera.remediation.models import ReconciliationBatch

    service = ApprovalService(InMemoryApprovalRepository(), InMemoryAuditLog(), world.events, world.clock,
                              ttl=timedelta(minutes=1))
    approval = service.request(payload=ReconciliationBatch(id="RB", incident_id="I", items=[], total_kobo=0),
                               subject_key="reconciliation:I", requested_by="agent")
    assert approval.expires_at - approval.requested_at == timedelta(minutes=1)
