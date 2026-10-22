"""Per-incident remediation state shared by the Blast Radius and Resolution agents."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from avera.remediation.common import Model
from avera.remediation.models import BlastRadius, FixPlan, ResolutionEvent, RouteChange, ValidatedHandoff


class RemediationStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    CONTAINING = "containing"
    CONTAINED = "contained"
    RESOLVED = "resolved"
    ROLLED_BACK = "rolled_back"
    ESCALATED = "escalated"


@dataclass
class RemediationCase:
    handoff: ValidatedHandoff
    plan: FixPlan
    blast_radius: BlastRadius
    status: RemediationStatus = RemediationStatus.AWAITING_APPROVAL
    approval_ids: dict[str, str] = field(default_factory=dict)
    executed_approvals: set[str] = field(default_factory=set)
    sms_retry_customers: dict[str, set[str]] = field(default_factory=dict)
    applied_change: RouteChange | None = None
    applied_at: datetime | None = None
    events: list[ResolutionEvent] = field(default_factory=list)

    @property
    def event_seq(self) -> int:
        return len(self.events)

    @property
    def incident_id(self) -> str:
        return self.plan.incident_id


class RemediationCaseSnapshot(Model):
    handoff: ValidatedHandoff
    plan: FixPlan
    blast_radius: BlastRadius
    status: RemediationStatus
    approval_ids: dict[str, str]
    executed_approvals: list[str]
    sms_retry_customers: dict[str, list[str]]
    applied_change: RouteChange | None = None
    applied_at: datetime | None = None
    events: list[ResolutionEvent]

    @classmethod
    def from_case(cls, case: RemediationCase) -> RemediationCaseSnapshot:
        return cls(
            handoff=case.handoff,
            plan=case.plan,
            blast_radius=case.blast_radius,
            status=case.status,
            approval_ids=case.approval_ids,
            executed_approvals=sorted(case.executed_approvals),
            sms_retry_customers={key: sorted(value) for key, value in case.sms_retry_customers.items()},
            applied_change=case.applied_change,
            applied_at=case.applied_at,
            events=case.events,
        )

    def to_case(self) -> RemediationCase:
        return RemediationCase(
            handoff=self.handoff,
            plan=self.plan,
            blast_radius=self.blast_radius,
            status=self.status,
            approval_ids=dict(self.approval_ids),
            executed_approvals=set(self.executed_approvals),
            sms_retry_customers={key: set(value) for key, value in self.sms_retry_customers.items()},
            applied_change=self.applied_change,
            applied_at=self.applied_at,
            events=list(self.events),
        )


class RemediationStore:
    def __init__(self) -> None:
        self._cases: dict[str, RemediationCase] = {}
        self._lock = threading.Lock()

    def get(self, incident_id: str) -> RemediationCase | None:
        return self._cases.get(incident_id)

    def save(self, case: RemediationCase) -> None:
        with self._lock:
            self._cases[case.incident_id] = case

    def list(self) -> list[RemediationCase]:
        return list(self._cases.values())

    def snapshot(self) -> list[dict]:
        return [RemediationCaseSnapshot.from_case(case).model_dump(mode="json") for case in self.list()]

    def restore(self, cases: list[dict]) -> None:
        restored = [RemediationCaseSnapshot.model_validate(item).to_case() for item in cases]
        with self._lock:
            self._cases = {case.incident_id: case for case in restored}

    def clear(self) -> None:
        with self._lock:
            self._cases.clear()
