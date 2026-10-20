"""Internal models for the Blast Radius and Resolution agents.

Inputs reuse the shared contracts in `avera.contracts` directly. Outputs are richer internal models that
`avera.remediation.wire` converts to the shared contracts before anything is published to /stream.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from avera.contracts import Hypothesis, Incident, TestResult
from avera.remediation.common import Model

# ---------------------------------------------------------------- inputs


class ValidatedHandoff(Model):
    """What the orchestrator passes once a hypothesis clears the Ghost Test threshold."""

    incident: Incident
    hypothesis: Hypothesis
    test_result: TestResult
    rivals: list[TestResult] = Field(default_factory=list)


class TransactionRecord(Model):
    """A transfer as the blast radius sees it. Koded's adapter builds these from transaction events."""

    txn_id: str
    customer_token: str
    ts: datetime
    amount_kobo: int = Field(ge=0)
    channel: str
    product: str
    route: str
    destination_bank: str
    error_code: str | None = None
    app_version: str | None = None
    debit_status: str
    credit_status: str

    def trait(self, name: str) -> str | None:
        value = getattr(self, name, None) if name in type(self).model_fields else None
        return None if value is None else str(value)


BehaviourKind = Literal[
    "retry", "history_refresh", "balance_refresh", "channel_switch", "otp_resend", "password_reset", "abandonment",
]


class BehaviourSignal(Model):
    ts: datetime
    customer_token: str
    kind: BehaviourKind
    txn_id: str | None = None


class RouteMetrics(Model):
    destination: str
    route: str
    window_minutes: int
    attempts: int
    errors: int

    @property
    def error_rate(self) -> float:
        return 0.0 if self.attempts == 0 else self.errors / self.attempts


# ---------------------------------------------------------------- approval vocabulary


class ApprovalLevel(IntEnum):
    AUTOMATIC = 1
    CUSTOMER_DISPUTE = 2
    ENGINEER = 3


class ApprovalKind(StrEnum):
    SMS = "sms"
    CONFIG = "config"
    CODE = "code"
    RECONCILIATION = "reconciliation"


class Role(StrEnum):
    """Human roles only. Agents are actors, never approvers."""

    CUSTOMER_DISPUTE = "customer_dispute"
    ENGINEER = "engineer"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


# ---------------------------------------------------------------- fix plan


class ActionType(StrEnum):
    """Every action an agent may plan. There is deliberately no action that moves money."""

    REROUTE = "reroute"
    PATCH = "patch"
    RECONCILIATION_PREP = "reconciliation_prep"
    NOTIFY_CUSTOMERS = "notify_customers"
    ALERT_ENGINEERS = "alert_engineers"


Track = Literal["containment", "permanent", "reconciliation", "communication"]


class RouteChange(Model):
    destination: str
    from_route: str
    to_route: str

    def inverse(self) -> RouteChange:
        return RouteChange(destination=self.destination, from_route=self.to_route, to_route=self.from_route)


class PlanStep(Model):
    id: str
    order: int
    track: Track
    action: ActionType
    title: str
    detail: str
    owner: str
    approval_kind: ApprovalKind | None = None
    eta_minutes: int = Field(ge=0)
    depends_on: list[str] = Field(default_factory=list)
    route_change: RouteChange | None = None


class RollbackSpec(Model):
    metric: Literal["error_rate"] = "error_rate"
    destination: str
    route: str
    threshold: float = Field(gt=0, lt=1)
    window_minutes: int = Field(gt=0)
    action: RouteChange


class VerificationSpec(Model):
    rerun_hypothesis_ids: list[str]
    live_signals: list[str]


class Estimate(Model):
    minutes: int = Field(ge=0)
    confidence: Literal["high", "low"]
    basis: str


class SimulationResult(Model):
    change: RouteChange
    samples: int
    success_rate: float = Field(ge=0, le=1)
    passed: bool


class FixPlan(Model):
    id: str
    incident_id: str
    hypothesis_id: str
    version: int = 1
    playbook: str
    cause: str
    cause_confidence: float
    impact_traits: list[str]
    steps: list[PlanStep]
    owners: list[str]
    rollback: RollbackSpec | None
    verification: VerificationSpec
    eta_containment: Estimate
    eta_full: Estimate
    simulation: SimulationResult | None
    created_at: datetime

    def step(self, track: Track) -> PlanStep | None:
        return next((s for s in self.steps if s.track == track), None)


# ---------------------------------------------------------------- blast radius


class AffectedCustomer(Model):
    customer_token: str
    txn_ids: list[str]
    exposure_kobo: int
    first_affected_at: datetime
    complained: bool
    behaviour_signals: list[str] = Field(default_factory=list)


class BlastRadius(Model):
    """affected = complained + silent, always. Behaviour signals never move a customer out of silent."""

    id: str
    incident_id: str
    matched_on: dict[str, str]
    window_start: datetime
    window_end: datetime
    txn_count: int
    customer_count: int
    complained_count: int
    silent_count: int
    silent_with_signals_count: int
    projected_count: int
    projection_basis: str
    exposure_kobo: int
    customers: list[AffectedCustomer]
    computed_at: datetime

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> BlastRadius:
        if self.complained_count + self.silent_count != self.customer_count:
            raise ValueError("complained + silent must equal affected customers")
        if self.customer_count != len(self.customers):
            raise ValueError("customer_count must match customers[]")
        if self.silent_with_signals_count > self.silent_count:
            raise ValueError("silent_with_signals cannot exceed silent")
        if self.txn_count != sum(len(c.txn_ids) for c in self.customers):
            raise ValueError("txn_count must match the customers' transactions")
        return self


# ---------------------------------------------------------------- approval payloads


class SmsMessage(Model):
    customer_token: str
    text: str


class SmsDraft(Model):
    kind: Literal[ApprovalKind.SMS] = ApprovalKind.SMS
    incident_id: str
    template: str
    eta_phrase: str
    eta_confidence: Literal["high", "low"]
    fallback_used: bool
    recipient_count: int
    messages: list[SmsMessage]


class ConfigChange(Model):
    kind: Literal[ApprovalKind.CONFIG] = ApprovalKind.CONFIG
    incident_id: str
    plan_id: str
    change: RouteChange
    rollback: RollbackSpec | None
    simulation: SimulationResult


class SandboxReport(Model):
    passed: bool
    tests_run: int
    tests_failed: int
    log_excerpt: str = ""


class CodePatch(Model):
    kind: Literal[ApprovalKind.CODE] = ApprovalKind.CODE
    incident_id: str
    plan_id: str
    hypothesis_id: str
    repository: str
    target: str
    summary: str
    diff: str
    sandbox: SandboxReport


class ReconciliationItem(Model):
    txn_id: str
    customer_token: str
    amount_kobo: int
    action: Literal["status_requery"] = "status_requery"
    on_unconfirmed: Literal["queue_for_reversal_review"] = "queue_for_reversal_review"


class ReconciliationBatch(Model):
    """A worklist for humans. Avera prepares it; a person always executes any financial action."""

    kind: Literal[ApprovalKind.RECONCILIATION] = ApprovalKind.RECONCILIATION
    id: str
    incident_id: str
    items: list[ReconciliationItem]
    total_kobo: int
    executed_by_avera: Literal[False] = False


ApprovalPayload = Annotated[
    Union[SmsDraft, ConfigChange, CodePatch, ReconciliationBatch],
    Field(discriminator="kind"),
]


class Approval(Model):
    id: str
    incident_id: str
    level: ApprovalLevel
    kind: ApprovalKind
    required_role: Role
    subject_key: str
    payload: ApprovalPayload
    payload_hash: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_by: str
    requested_at: datetime
    expires_at: datetime | None = None
    supersedes: str | None = None
    approver: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None


# ---------------------------------------------------------------- resolution


class EngineerAlert(Model):
    incident_id: str
    hypothesis_id: str
    severity: Literal["high", "critical"]
    cause: str
    confidence: float
    evidence: list[str]
    affected_components: list[str]
    recommended_next_step: str
    routed_to: list[str]


class ResolutionAction(StrEnum):
    ROUTE_APPLIED = "apply_approved_reroute"
    ROUTE_ROLLED_BACK = "rollback_reroute"
    VERIFIED = "verify_after_approved_reroute"
    PATCH_OPENED = "open_connector_patch_for_review"
    PATCH_APPROVED = "approve_connector_patch"
    ALERT_RAISED = "alert_engineers"
    RECONCILIATION_HANDED_OFF = "hand_off_reconciliation_batch"
    SMS_SENT = "send_approved_sms"
    APPROVAL_REJECTED = "record_rejection"
    INCIDENT_RESOLVED = "mark_incident_resolved"


class ResolutionEvent(Model):
    id: str
    incident_id: str
    ts: datetime
    action: ResolutionAction
    status: str
    approval_id: str | None = None
    diff_url: str | None = None
    alert: EngineerAlert | None = None
    verified_by_ghost_test: bool = False
    detail: dict = Field(default_factory=dict)
