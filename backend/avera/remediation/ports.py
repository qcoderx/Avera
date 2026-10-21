"""Everything the remediation agents need from the rest of Avera, expressed as interfaces.

Koded implements these against the real system; `avera.remediation.fixtures` implements them for standalone work.
No port exposes a way to move money. `tests/remediation/test_guardrails.py` enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from avera.contracts import Complaint, ContractModel, ContractObjectType, Hypothesis, TestResult
from avera.remediation.models import (
    BehaviourSignal,
    RouteChange,
    RouteMetrics,
    SandboxReport,
    SimulationResult,
    TransactionRecord,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class BankData(Protocol):
    """Read-only access to transfers, complaints and behaviour signals."""

    def find_transactions(self, traits: dict[str, str], start: datetime, end: datetime) -> list[TransactionRecord]: ...

    def complaints_between(self, start: datetime, end: datetime) -> list[Complaint]: ...

    def behaviour_signals_between(self, start: datetime, end: datetime) -> list[BehaviourSignal]: ...


class DigitalTwin(Protocol):
    """POST /twin/simulate"""

    def simulate_route(self, change: RouteChange) -> SimulationResult: ...


class Routing(Protocol):
    """POST /admin/route. Only ever called with an approved change."""

    def apply_route(self, change: RouteChange) -> str: ...


class GhostTests(Protocol):
    """POST /ghost-test/rerun. Returns TestResults with purpose=post_fix_rerun."""

    def rerun(self, incident_id: str, hypothesis_ids: list[str]) -> list[TestResult]: ...


class Metrics(Protocol):
    """Live metrics for rollback triggers and the 'signals back to normal' check."""

    def route_metrics(self, destination: str, route: str, window_minutes: int) -> RouteMetrics: ...

    def live_signals_normal(self, incident_id: str, signals: list[str]) -> dict[str, bool]: ...


class Sandbox(Protocol):
    def run_patch(self, repository: str, target: str, diff: str) -> SandboxReport: ...


@dataclass(frozen=True)
class PatchProposal:
    repository: str
    target: str
    summary: str
    diff: str


class PatchAuthor(Protocol):
    def write_patch(self, hypothesis: Hypothesis, test_result: TestResult) -> PatchProposal: ...


@dataclass(frozen=True)
class Contact:
    phone_e164: str
    sms_opt_in: bool


class ContactDirectory(Protocol):
    """Resolves a customer token to contact details at send time only. Results are never stored."""

    def resolve(self, customer_token: str) -> Contact | None: ...


class SmsGateway(Protocol):
    """The provider must deduplicate repeated sends with the same key."""

    def send(self, to: str, text: str, idempotency_key: str) -> str: ...


class EventPublisher(Protocol):
    """Feeds GET /stream. Koded's implementation assigns the sequence number and wraps it in a StreamRecord."""

    def publish(self, object_type: ContractObjectType, data: ContractModel) -> None: ...


@dataclass(frozen=True)
class Ports:
    clock: Clock
    bank: BankData
    twin: DigitalTwin
    routing: Routing
    ghost_tests: GhostTests
    metrics: Metrics
    sandbox: Sandbox
    patch_author: PatchAuthor
    contacts: ContactDirectory
    sms: SmsGateway
    events: EventPublisher
