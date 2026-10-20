from __future__ import annotations

import pytest

from avera.remediation.approvals import Approver
from avera.remediation.blast_radius import BlastRadiusConfig
from avera.remediation.fixtures import DISPLAY_TZ, DemoWorld, build_demo_world
from avera.remediation.models import Approval, Role
from avera.remediation.pipeline import Remediation, build_remediation
from avera.remediation.store import RemediationCase

DISPUTE = Approver(id="disputes-demo-user", role=Role.CUSTOMER_DISPUTE)
ENGINEER = Approver(id="engineer-demo-user", role=Role.ENGINEER)


@pytest.fixture
def world() -> DemoWorld:
    return build_demo_world()


@pytest.fixture
def remediation(world: DemoWorld) -> Remediation:
    return build_remediation(world.ports, BlastRadiusConfig(display_tz=DISPLAY_TZ))


@pytest.fixture
def case(world: DemoWorld, remediation: Remediation) -> RemediationCase:
    return remediation.on_validated(world.handoff)


def approve(remediation: Remediation, approval_id: str, approver: Approver) -> Approval:
    approval = remediation.approvals.get(approval_id)
    return remediation.approvals.decide(approval_id, decision="approve", approver=approver,
                                        payload_hash=approval.payload_hash)
