from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Literal, Protocol

from avera.contracts import ContractObjectType
from avera.remediation import wire
from avera.remediation.approvals.policy import policy_for
from avera.remediation.audit import AuditLog
from avera.remediation.common import canonical_hash
from avera.remediation.models import Approval, ApprovalPayload, ApprovalStatus, Role
from avera.remediation.ports import Clock, EventPublisher

log = logging.getLogger(__name__)

Decision = Literal["approve", "reject"]
DecisionListener = Callable[[Approval], None]


class ApprovalError(Exception):
    pass


class ApprovalNotFound(ApprovalError):
    pass


class ApprovalNotPending(ApprovalError):
    pass


class ApproverNotAuthorised(ApprovalError):
    pass


class StalePayload(ApprovalError):
    """The approver saw a different payload from the one currently awaiting approval."""


@dataclass(frozen=True)
class Approver:
    id: str
    role: Role


class ApprovalRepository(Protocol):
    def get(self, approval_id: str) -> Approval | None: ...

    def save(self, approval: Approval) -> None: ...

    def list(self, *, incident_id: str | None = None, status: ApprovalStatus | None = None) -> list[Approval]: ...

    def clear(self) -> None: ...


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}

    def get(self, approval_id: str) -> Approval | None:
        return self._items.get(approval_id)

    def save(self, approval: Approval) -> None:
        self._items[approval.id] = approval

    def list(self, *, incident_id: str | None = None, status: ApprovalStatus | None = None) -> list[Approval]:
        return [
            a
            for a in sorted(self._items.values(), key=lambda a: a.requested_at)
            if (incident_id is None or a.incident_id == incident_id) and (status is None or a.status == status)
        ]

    def clear(self) -> None:
        self._items.clear()


class ApprovalService:
    """The only way anything in Avera reaches a customer or production."""

    def __init__(
        self,
        repo: ApprovalRepository,
        audit: AuditLog,
        events: EventPublisher,
        clock: Clock,
        ttl: timedelta | None = timedelta(hours=4),
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ttl = ttl
        self._listeners: list[DecisionListener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: DecisionListener) -> None:
        self._listeners.append(listener)

    def get(self, approval_id: str) -> Approval:
        approval = self._repo.get(approval_id)
        if approval is None:
            raise ApprovalNotFound(approval_id)
        return approval

    def list(self, *, incident_id: str | None = None, status: ApprovalStatus | None = None) -> list[Approval]:
        return self._repo.list(incident_id=incident_id, status=status)

    def snapshot(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in self._repo.list()]

    def restore(self, items: list[dict]) -> None:
        restored = [Approval.model_validate(item) for item in items]
        if any(canonical_hash(item.payload) != item.payload_hash for item in restored):
            raise ValueError("persisted approval payload hash does not match its payload")
        with self._lock:
            self._repo.clear()
            for item in restored:
                self._repo.save(item)

    def reset_demo_state(self) -> None:
        """Close pending demo decisions and clear them while retaining the audit trail."""
        now = self._clock.now()
        with self._lock:
            for approval in self._repo.list(status=ApprovalStatus.PENDING):
                self._transition(
                    approval.model_copy(update={"status": ApprovalStatus.SUPERSEDED, "decided_at": now}),
                    actor="system", action="demo_approval_reset",
                )
            self._repo.clear()

    def request(self, *, payload: ApprovalPayload, subject_key: str, requested_by: str) -> Approval:
        """Raise an approval. A newer request for the same subject supersedes any pending one."""
        level, role = policy_for(payload.kind)
        now = self._clock.now()
        with self._lock:
            superseded = [
                a for a in self._repo.list(incident_id=payload.incident_id, status=ApprovalStatus.PENDING)
                if a.subject_key == subject_key
            ]
            for old in superseded:
                self._transition(old.model_copy(update={"status": ApprovalStatus.SUPERSEDED, "decided_at": now}),
                                 actor=requested_by, action="approval_superseded")
            previous = [a for a in self._repo.list(incident_id=payload.incident_id) if a.subject_key == subject_key]
            approval_id = f"approval-{payload.kind}-{payload.incident_id}"
            approval = Approval(
                id=approval_id if not previous else f"{approval_id}-v{len(previous) + 1}",
                incident_id=payload.incident_id,
                level=level,
                kind=payload.kind,
                required_role=role,
                subject_key=subject_key,
                payload=payload,
                payload_hash=canonical_hash(payload),
                requested_by=requested_by,
                requested_at=now,
                expires_at=now + self._ttl if self._ttl else None,
                supersedes=superseded[-1].id if superseded else None,
            )
            self._transition(approval, actor=requested_by, action="approval_requested")
        return approval

    def withdraw(self, *, incident_id: str, subject_key: str, requested_by: str) -> None:
        """Retire a pending request whose plan no longer needs it."""
        now = self._clock.now()
        with self._lock:
            for old in self._repo.list(incident_id=incident_id, status=ApprovalStatus.PENDING):
                if old.subject_key == subject_key:
                    self._transition(old.model_copy(update={"status": ApprovalStatus.SUPERSEDED, "decided_at": now}),
                                     actor=requested_by, action="approval_withdrawn")

    def decide(
        self,
        approval_id: str,
        *,
        decision: Decision,
        approver: Approver,
        payload_hash: str,
        reason: str | None = None,
    ) -> Approval:
        now = self._clock.now()
        with self._lock:
            approval = self.get(approval_id)
            if approval.status != ApprovalStatus.PENDING:
                raise ApprovalNotPending(f"{approval_id} is {approval.status}")
            if approval.expires_at is not None and now >= approval.expires_at:
                self._transition(approval.model_copy(update={"status": ApprovalStatus.EXPIRED, "decided_at": now}),
                                 actor="system", action="approval_expired")
                raise ApprovalNotPending(f"{approval_id} expired")
            if approver.role != approval.required_role:
                raise ApproverNotAuthorised(f"{approval.kind} approvals require role {approval.required_role}")
            if approver.id == approval.requested_by:
                raise ApproverNotAuthorised("requester cannot approve their own request")
            if payload_hash != approval.payload_hash:
                raise StalePayload(approval_id)
            if decision == "reject" and not reason:
                raise ApprovalError("a rejection needs a reason")

            decided = approval.model_copy(update={
                "status": ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED,
                "approver": approver.id,
                "decided_at": now,
                "decision_reason": reason,
            })
            self._transition(decided, actor=approver.id, action=f"approval_{decided.status}",
                             detail={"role": str(approver.role)})

        for listener in self._listeners:
            try:
                listener(decided)
            except Exception:
                log.exception("approval listener failed for %s", decided.id)
                self._audit.append(ts=self._clock.now(), actor="system", action="approval_dispatch_failed",
                                   incident_id=decided.incident_id, subject_type="approval",
                                   subject_id=decided.id, payload=decided.payload_hash)
        return decided

    def _transition(self, approval: Approval, *, actor: str, action: str, detail: dict | None = None) -> None:
        self._repo.save(approval)
        self._audit.append(
            ts=self._clock.now(), actor=actor, action=action, incident_id=approval.incident_id,
            subject_type="approval", subject_id=approval.id, payload=approval.payload_hash,
            detail={"kind": str(approval.kind), "level": int(approval.level), **(detail or {})},
        )
        shared = wire.approval(approval)
        if shared is not None:
            self._events.publish(ContractObjectType.APPROVAL, shared)
