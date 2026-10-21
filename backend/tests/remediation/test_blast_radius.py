from __future__ import annotations

import pytest

from avera.remediation.blast_radius import BlastRadiusConfig, HandoffRejected
from avera.remediation.blast_radius.eta import critical_path_minutes
from avera.remediation.blast_radius.sms import FALLBACK_ETA_PHRASE, SmsPolicyViolation, check_sms_policy, eta_phrase
from avera.remediation.fixtures import DISPLAY_TZ, build_demo_world
from avera.remediation.models import ActionType, ApprovalKind, Estimate, PlanStep
from avera.remediation.pipeline import build_remediation


def test_demo_numbers_come_from_the_seeded_data(case):
    br = case.blast_radius
    assert (br.txn_count, br.customer_count, br.complained_count, br.silent_count) == (1284, 1109, 13, 1096)
    assert 0 < br.silent_with_signals_count < br.silent_count


def test_scope_ignores_the_decoy_app_version(case, world):
    assert "app_version" in world.handoff.incident.fingerprint
    assert "app_version" not in case.blast_radius.matched_on
    affected = {t for c in case.blast_radius.customers for t in c.txn_ids}
    versions = {t.app_version for t in world.bank_data.transactions if t.txn_id in affected}
    assert {"4.1", "4.2", None} <= versions


def test_behaviour_signals_never_count_as_complaints(case):
    silent_with_signals = [c for c in case.blast_radius.customers if not c.complained and c.behaviour_signals]
    assert len(silent_with_signals) == case.blast_radius.silent_with_signals_count


def test_complainers_are_exactly_the_parser_matched_customers(case, world):
    complainers = {c.customer_token for c in case.blast_radius.customers if c.complained}
    assert complainers == {c.customer_token for c in world.bank_data.complaints}


def test_blast_radius_is_identical_across_runs():
    def run():
        world = build_demo_world()
        return build_remediation(world.ports).on_validated(world.handoff).blast_radius.model_dump()

    assert run() == run()


def test_stream_objects_validate_against_shared_contracts(case, world):
    from avera.contracts import BlastRadius, ContractObjectType, FixPlan

    br = BlastRadius.model_validate(world.events.of_type(ContractObjectType.BLAST_RADIUS)[0])
    assert (br.id, br.txn_count, br.customer_count, br.silent_count) == ("blast-AV-48291", 1284, 1109, 1096)
    assert br.customers_complete and len(br.customers) == 1109
    plan = FixPlan.model_validate(world.events.of_type(ContractObjectType.FIX_PLAN)[0])
    assert plan.id == "plan-AV-48291"
    assert plan.eta_containment == "10 minutes after approval"
    assert plan.rollback.startswith("Restore gateway_b.bank_x routing if gateway_c.bank_x error rate exceeds 2%")


def test_plan_matches_the_prd_fix(case):
    plan = case.plan
    containment = plan.step("containment")
    assert containment.action is ActionType.REROUTE
    assert (containment.route_change.from_route, containment.route_change.to_route) == ("gateway_b.bank_x", "gateway_c.bank_x")
    assert plan.step("permanent").approval_kind is ApprovalKind.CODE
    assert plan.rollback.action.to_route == "gateway_b.bank_x"
    assert plan.simulation.success_rate == pytest.approx(0.967)
    assert (plan.eta_containment.minutes, plan.eta_full.minutes) == (10, 240)


def test_demo_sms_uses_prd_wording_with_eta_from_plan(case, remediation):
    draft = remediation.approvals.get(case.approval_ids["sms"]).payload
    text = next(m.text for m in draft.messages if m.customer_token == "cust_demo_0001")
    assert text == (
        "We're sorry. We found an issue affecting your N34,500 transfer at 10:42 AM. You do not need to contact us. "
        "We are fixing it now and expect it to be resolved within 4 hours. Ref: AV-48291."
    )
    assert draft.recipient_count == 1109


def test_sms_renders_in_lagos_time_by_default(world):
    remediation = build_remediation(world.ports)
    case = remediation.on_validated(world.handoff)
    draft = remediation.approvals.get(case.approval_ids["sms"]).payload
    text = next(m.text for m in draft.messages if m.customer_token == "cust_demo_0001")
    assert "N34,500 transfer at 11:42 AM" in text


def test_low_confidence_eta_uses_soft_wording():
    phrase, fallback = eta_phrase(Estimate(minutes=240, confidence="low", basis="x"))
    assert (phrase, fallback) == (FALLBACK_ETA_PHRASE, True)


@pytest.mark.parametrize("text", [
    "Visit https://bank.example to fix it",
    "Your account 0123456789 was affected",
    "Reply with your PIN",
    "Your ₦34,500 transfer",
    "x" * 400,
])
def test_sms_policy_blocks_unsafe_content(text):
    with pytest.raises(SmsPolicyViolation):
        check_sms_policy(text)


def test_failed_simulation_falls_back_to_engineer_alert(world):
    from avera.contracts import ContractObjectType

    world.bank.broken.add("gateway_c.bank_x")
    case = build_remediation(world.ports, BlastRadiusConfig(display_tz=DISPLAY_TZ)).on_validated(world.handoff)
    assert case.plan.playbook == "out_of_reach"
    assert "config" not in case.approval_ids
    assert case.plan.eta_full.confidence == "low"
    assert any(e["action"] == "alert_engineers" for e in world.events.of_type(ContractObjectType.RESOLUTION_EVENT))


def test_handoff_below_threshold_is_rejected(world, remediation):
    weak = world.handoff.model_copy(update={"test_result": world.handoff.test_result.model_copy(
        update={"confidence": 0.80})})
    with pytest.raises(HandoffRejected):
        remediation.on_validated(weak)


def test_handoff_with_missing_impact_scope_is_rejected_before_blast_radius_query(world, remediation):
    unscoped_incident = world.handoff.incident.model_copy(update={"fingerprint": {}})
    handoff = world.handoff.model_copy(update={"incident": unscoped_incident})
    with pytest.raises(HandoffRejected, match="missing impact-scope traits"):
        remediation.on_validated(handoff)
    assert remediation.approvals.list() == []


def test_repeated_handoff_is_idempotent(world, remediation):
    first = remediation.on_validated(world.handoff)
    second = remediation.on_validated(world.handoff)
    assert first is second
    assert len(remediation.approvals.list()) == 3


def test_critical_path_respects_dependencies():
    def step(step_id, minutes, deps=()):
        return PlanStep(id=step_id, order=1, track="permanent", action=ActionType.PATCH, title="t", detail="d",
                        owner="o", eta_minutes=minutes, depends_on=list(deps))

    assert critical_path_minutes([step("A", 10), step("B", 30), step("C", 5, ["A"])]) == 30
    assert critical_path_minutes([step("A", 10), step("C", 25, ["A"])]) == 35
