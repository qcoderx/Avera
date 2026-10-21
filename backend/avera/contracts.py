"""Shared, versioned contracts used across the Avera demo services.

These models are the integration boundary between Koded's backend, Shaz's
Observability Agent, Adam's resolution agents, and Platinum's UI. Keep wire
field names aligned with ``private/Avera_Work_Split.pdf``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Base settings for contract payloads shared between team components."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IncidentStatus(StrEnum):
    EMERGING = "emerging"
    INVESTIGATING = "investigating"
    VALIDATED = "validated"
    PLANNED = "planned"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TestVerdict(StrEnum):
    VALIDATED = "validated"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class TestRunPurpose(StrEnum):
    HYPOTHESIS_VALIDATION = "hypothesis_validation"
    POST_FIX_RERUN = "post_fix_rerun"


class ApprovalLevel(StrEnum):
    CUSTOMER_DISPUTE = "level_2"
    ENGINEER = "level_3"


class ApprovalKind(StrEnum):
    SMS = "sms"
    CONFIG = "config"
    CODE = "code"
    RECONCILIATION = "reconciliation"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ContractObjectType(StrEnum):
    EVENT = "event"
    COMPLAINT = "complaint"
    INCIDENT = "incident"
    HYPOTHESIS = "hypothesis"
    TEST_RESULT = "test_result"
    FIX_PLAN = "fix_plan"
    BLAST_RADIUS = "blast_radius"
    APPROVAL = "approval"
    RESOLUTION_EVENT = "resolution_event"


class Event(ContractModel):
    id: str = Field(min_length=1, description="Stable source or ingestion event ID")
    ts: datetime
    source: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    service: str = Field(min_length=1)
    route: str | None = None
    code: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    customer_token: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ParsedComplaint(ContractModel):
    type: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    product: str | None = None
    time: datetime | None = None
    matched_txn: str | None = None


class Complaint(ContractModel):
    id: str = Field(min_length=1)
    ts: datetime
    channel: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    customer_token: str | None = None
    parsed: ParsedComplaint | None = None


class Incident(ContractModel):
    id: str = Field(min_length=1)
    status: IncidentStatus
    opened_at: datetime
    signals: list[str] = Field(default_factory=list)
    fingerprint: dict[str, Any]
    suspect_zones: list[str] = Field(default_factory=list)


class Hypothesis(ContractModel):
    id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    disproof: str = Field(min_length=1)
    routed_to: list[str] = Field(default_factory=list)
    prior_rank: int = Field(ge=1)


class TraceStep(ContractModel):
    service: str = Field(min_length=1)
    result: str = Field(min_length=1)
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class ControlResult(ContractModel):
    route: str = Field(min_length=1)
    runs: int = Field(ge=1)
    reproduced: int = Field(ge=0)

    @model_validator(mode="after")
    def reproduced_cannot_exceed_runs(self) -> ControlResult:
        if self.reproduced > self.runs:
            raise ValueError("control reproduced cannot exceed control runs")
        return self


class TestResult(ContractModel):
    id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    purpose: TestRunPurpose = TestRunPurpose.HYPOTHESIS_VALIDATION
    test_plan: str = Field(min_length=1)
    runs: int = Field(ge=1)
    reproduced: int = Field(ge=0)
    trace: list[TraceStep] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    verdict: TestVerdict
    control: ControlResult | None = None
    disproof_passed: bool | None = None
    rerun_passed: bool | None = None

    @model_validator(mode="after")
    def reproduced_cannot_exceed_runs(self) -> TestResult:
        if self.reproduced > self.runs:
            raise ValueError("reproduced cannot exceed runs")
        if self.purpose == TestRunPurpose.POST_FIX_RERUN and self.rerun_passed is None:
            raise ValueError("post-fix reruns must state whether verification passed")
        if self.purpose == TestRunPurpose.HYPOTHESIS_VALIDATION and self.rerun_passed is not None:
            raise ValueError("hypothesis validation cannot set rerun_passed")
        return self


class FixStep(ContractModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner: str = Field(min_length=1)


class FixPlan(ContractModel):
    id: str = Field(min_length=1)
    steps: list[FixStep] = Field(min_length=1)
    owners: list[str] = Field(min_length=1)
    rollback: str = Field(min_length=1)
    verification: str = Field(min_length=1)
    eta_containment: str = Field(min_length=1)
    eta_full: str = Field(min_length=1)


class BlastRadius(ContractModel):
    id: str = Field(min_length=1)
    txn_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    silent_count: int = Field(ge=0)
    exposure_ngn: Decimal = Field(ge=0)
    customers: list[str] = Field(default_factory=list)
    customers_complete: bool = Field(
        default=True,
        description="False when customers contains a display sample rather than every affected token.",
    )


class Approval(ContractModel):
    id: str = Field(min_length=1)
    level: ApprovalLevel
    kind: ApprovalKind
    payload: dict[str, Any]
    status: ApprovalStatus
    approver: str | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def decision_fields_match_status(self) -> Approval:
        has_decision = self.approver is not None and self.decided_at is not None
        if self.status == ApprovalStatus.PENDING and (self.approver or self.decided_at):
            raise ValueError("pending approvals cannot have approver or decided_at")
        if self.status != ApprovalStatus.PENDING and not has_decision:
            raise ValueError("decided approvals require approver and decided_at")
        return self


class ResolutionEvent(ContractModel):
    id: str = Field(min_length=1)
    incident_id: str | None = None
    ts: datetime | None = None
    action: str = Field(min_length=1)
    status: str = Field(min_length=1)
    approval_id: str | None = None
    diff_url: str | None = None
    alert: dict[str, Any] | None = None
    verified_by_ghost_test: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class StreamRecord(ContractModel):
    """Ordered envelope used by fixture replay and the ``GET /stream`` API."""

    sequence: int = Field(ge=1)
    type: ContractObjectType
    occurred_at: datetime
    data: dict[str, Any]

    _payload_models: ClassVar[dict[ContractObjectType, type[ContractModel]]] = {}

    def validate_payload(self) -> ContractModel:
        model = self._payload_models.get(self.type)
        if model is None:
            raise ValueError(f"No payload model registered for {self.type.value}")
        return model.model_validate(self.data)


class IncidentFixture(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    scenario: str = Field(min_length=1)
    records: list[StreamRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order_and_payloads(self) -> IncidentFixture:
        expected = list(range(1, len(self.records) + 1))
        actual = [record.sequence for record in self.records]
        if actual != expected:
            raise ValueError("fixture sequence numbers must be contiguous and start at 1")
        for record in self.records:
            record.validate_payload()
        return self


StreamRecord._payload_models = {
    ContractObjectType.EVENT: Event,
    ContractObjectType.COMPLAINT: Complaint,
    ContractObjectType.INCIDENT: Incident,
    ContractObjectType.HYPOTHESIS: Hypothesis,
    ContractObjectType.TEST_RESULT: TestResult,
    ContractObjectType.FIX_PLAN: FixPlan,
    ContractObjectType.BLAST_RADIUS: BlastRadius,
    ContractObjectType.APPROVAL: Approval,
    ContractObjectType.RESOLUTION_EVENT: ResolutionEvent,
}
