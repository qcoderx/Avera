"""Per-incident remediation state shared by the Blast Radius and Resolution agents."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

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
    applied_change: RouteChange | None = None
    applied_at: datetime | None = None
    events: list[ResolutionEvent] = field(default_factory=list)

    @property
    def event_seq(self) -> int:
        return len(self.events)

    @property
    def incident_id(self) -> str:
        return self.plan.incident_id


class RemediationStore:
    def __init__(self) -> None:
        self._cases: dict[str, RemediationCase] = {}
        self._lock = threading.Lock()

    def get(self, incident_id: str) -> RemediationCase | None:
        return self._cases.get(incident_id)

    def save(self, case: RemediationCase) -> None:
        with self._lock:
            self._cases[case.incident_id] = case
