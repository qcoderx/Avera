"""Hard guarantees from the PRD: Avera never moves money, and nothing acts without a recorded approval."""

from __future__ import annotations

import inspect
import re

import pytest
from pydantic import ValidationError

from avera.remediation import ports
from avera.remediation.models import ActionType, ReconciliationBatch

MONEY_VERBS = re.compile(r"debit|credit|transfer|revers|refund|settle|payout|disburse|post_ledger|move_funds", re.I)


def _port_methods():
    for _, cls in inspect.getmembers(ports, inspect.isclass):
        if getattr(cls, "_is_protocol", False):
            for name, _ in inspect.getmembers(cls, inspect.isfunction):
                yield cls.__name__, name


def test_no_port_can_move_money():
    offenders = [f"{cls}.{name}" for cls, name in _port_methods() if MONEY_VERBS.search(name)]
    assert offenders == []


def test_no_planned_action_moves_money():
    assert not [a for a in ActionType if MONEY_VERBS.search(a.value)]


def test_reconciliation_batch_cannot_claim_execution():
    with pytest.raises(ValidationError):
        ReconciliationBatch(id="RB", incident_id="I", items=[], total_kobo=0, executed_by_avera=True)


def test_every_executed_action_has_a_recorded_approval(case, remediation, world):
    from tests.remediation.conftest import DISPUTE, ENGINEER, approve

    approve(remediation, case.approval_ids["sms"], DISPUTE)
    approve(remediation, case.approval_ids["config"], ENGINEER)
    from avera.remediation.models import ResolutionAction

    approved = {a.id for a in remediation.approvals.list() if a.status == "approved"}
    gated = {ResolutionAction.ROUTE_APPLIED, ResolutionAction.SMS_SENT, ResolutionAction.RECONCILIATION_HANDED_OFF,
             ResolutionAction.PATCH_APPROVED}
    gated_events = [e for e in case.events if e.action in gated]
    assert gated_events
    for event in gated_events:
        assert event.approval_id in approved
