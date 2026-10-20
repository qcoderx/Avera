"""Walk the remediation half of the demo on fixture data: python -m avera.remediation.demo"""

from __future__ import annotations

from avera.contracts import ContractObjectType
from avera.remediation.approvals import Approver
from avera.remediation.blast_radius import BlastRadiusConfig
from avera.remediation.common import format_naira
from avera.remediation.fixtures import DISPLAY_TZ, build_demo_world
from avera.remediation.models import Role
from avera.remediation.pipeline import build_remediation


def main() -> None:
    world = build_demo_world()
    remediation = build_remediation(world.ports, BlastRadiusConfig(display_tz=DISPLAY_TZ))
    approvals = remediation.approvals

    handoff = world.handoff
    print(f"== {handoff.hypothesis.id} validated at {handoff.test_result.confidence:.0%}; "
          f"rivals {', '.join(f'{r.hypothesis_id} {r.confidence:.0%}' for r in handoff.rivals)} ==")
    case = remediation.on_validated(handoff)
    plan, br = case.plan, case.blast_radius

    print(f"\nMatched on {br.matched_on}")
    print(f"Complained {br.complained_count:,} | affected {br.customer_count:,} | silent {br.silent_count:,} "
          f"({br.silent_with_signals_count:,} showed distress) | {br.txn_count:,} transactions | "
          f"exposure {format_naira(br.exposure_kobo)}")
    print(f"Projected newly affected before containment: {br.projected_count:,} ({br.projection_basis})")

    print(f"\nFix plan {plan.id} ({plan.playbook})")
    for step in sorted(plan.steps, key=lambda s: s.order):
        gate = f"needs {step.approval_kind} approval" if step.approval_kind else "automatic"
        print(f"  {step.order}. [{step.track}] {step.title} - {step.owner}, ~{step.eta_minutes} min, {gate}")
    print(f"  Twin simulation {plan.simulation.success_rate:.1%}; ETA containment {plan.eta_containment.minutes} min, "
          f"full {plan.eta_full.minutes} min ({plan.eta_full.confidence} confidence)")

    sms = approvals.get(case.approval_ids["sms"])
    demo_text = next(m.text for m in sms.payload.messages if m.customer_token == "cust_demo_0001")
    print(f"\nSMS awaiting Customer Dispute approval ({sms.payload.recipient_count:,} recipients):\n  {demo_text}")

    print("\n== Approvals ==")
    for kind, approver in [("sms", Approver("disputes-demo-user", Role.CUSTOMER_DISPUTE)),
                           ("reconciliation", Approver("disputes-demo-user", Role.CUSTOMER_DISPUTE)),
                           ("config", Approver("engineer-demo-user", Role.ENGINEER))]:
        world.clock.advance(minutes=3)
        approval = approvals.get(case.approval_ids[kind])
        approvals.decide(approval.id, decision="approve", approver=approver, payload_hash=approval.payload_hash)
        print(f"  {approver.id} approved {approval.id} (level {int(approval.level)})")

    print("\n== Published to /stream (shared contracts) ==")
    for object_type, data in world.events.records:
        summary = getattr(data, "action", None) or getattr(data, "kind", None) or ""
        status = getattr(data, "status", "")
        print(f"  {object_type.value:<17} {data.id:<36} {summary} {status}")

    print(f"\nRemediation status: {case.status}. Audit entries: {len(remediation.audit.entries())}, "
          f"chain intact: {remediation.audit.verify()}")
    published = {x["id"] for x in world.events.of_type(ContractObjectType.APPROVAL)}
    held_back = [a.id for a in approvals.list() if a.id not in published]
    if held_back:
        print(f"Held back from /stream until the shared contract supports them: {held_back}")


if __name__ == "__main__":
    main()
