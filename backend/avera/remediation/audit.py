"""Append-only, hash-chained audit log for every plan, approval request, decision and action."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Protocol

from avera.remediation.common import Model, canonical_hash

GENESIS_HASH = "0" * 64


class AuditEntry(Model):
    seq: int
    ts: datetime
    actor: str
    action: str
    incident_id: str
    subject_type: str
    subject_id: str
    payload_hash: str
    detail: dict[str, Any]
    prev_hash: str
    hash: str


def _entry_hash(fields: dict[str, Any]) -> str:
    return canonical_hash({k: v for k, v in fields.items() if k != "hash"})


class AuditLog(Protocol):
    def append(
        self,
        *,
        ts: datetime,
        actor: str,
        action: str,
        incident_id: str,
        subject_type: str,
        subject_id: str,
        payload: Any = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry: ...

    def entries(self, incident_id: str | None = None) -> list[AuditEntry]: ...

    def verify(self) -> bool: ...

    def restore(self, entries: list[dict]) -> None: ...


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def append(
        self,
        *,
        ts: datetime,
        actor: str,
        action: str,
        incident_id: str,
        subject_type: str,
        subject_id: str,
        payload: Any = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry:
        with self._lock:
            prev_hash = self._entries[-1].hash if self._entries else GENESIS_HASH
            fields: dict[str, Any] = {
                "seq": len(self._entries) + 1,
                "ts": ts,
                "actor": actor,
                "action": action,
                "incident_id": incident_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "payload_hash": canonical_hash(payload),
                "detail": detail or {},
                "prev_hash": prev_hash,
            }
            entry = AuditEntry(**fields, hash="")
            entry = entry.model_copy(update={"hash": _entry_hash(entry.model_dump(mode="json"))})
            self._entries.append(entry)
            return entry

    def entries(self, incident_id: str | None = None) -> list[AuditEntry]:
        return [e for e in self._entries if incident_id is None or e.incident_id == incident_id]

    def verify(self) -> bool:
        prev_hash = GENESIS_HASH
        for expected_seq, entry in enumerate(self._entries, start=1):
            if entry.seq != expected_seq or entry.prev_hash != prev_hash:
                return False
            if _entry_hash(entry.model_dump(mode="json")) != entry.hash:
                return False
            prev_hash = entry.hash
        return True

    def restore(self, entries: list[dict]) -> None:
        restored = [AuditEntry.model_validate(item) for item in entries]
        with self._lock:
            previous = self._entries
            self._entries = restored
            if not self.verify():
                self._entries = previous
                raise ValueError("persisted remediation audit chain is invalid")
