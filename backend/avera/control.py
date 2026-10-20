"""Isolated twin simulations, in-platform approvals, and demo lifecycle controls."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from avera.contracts import (
    Approval,
    ApprovalKind,
    ApprovalLevel,
    ApprovalStatus,
    ContractModel,
    ResolutionEvent,
    TraceStep,
)
from avera.events.service import EventStreamService
from avera.ghost_test import GhostTestAgent
from avera.simulation.bank import DESTINATION_BANKS, GATEWAYS, SimulatedBank, TransferResult, TransferStatus
from avera.simulation.failures import FailureInjector
from avera.simulation.traffic import TrafficGenerator, TrafficSummary


class TwinSimulationRequest(ContractModel):
    destination_bank: str = "BANK_X"
    to_gateway: str
    runs: int = Field(default=5, ge=1, le=20)

    @field_validator("destination_bank")
    @classmethod
    def supported_bank(cls, value: str) -> str:
        if value not in DESTINATION_BANKS:
            raise ValueError(f"unsupported destination bank: {value}")
        return value

    @field_validator("to_gateway")
    @classmethod
    def supported_gateway(cls, value: str) -> str:
        if value not in GATEWAYS:
            raise ValueError(f"unsupported gateway: {value}")
        return value


class TwinSimulationReceipt(ContractModel):
    destination_bank: str
    from_gateway: str
    to_gateway: str
    runs: int
    baseline_completed: int
    candidate_completed: int
    baseline_failures: int
    candidate_failures: int
    improvement: int
    trace: list[TraceStep]
    isolated: bool = True
    live_route_unchanged: bool = True


class ApprovalCreateRequest(ContractModel):
    level: ApprovalLevel
    kind: ApprovalKind
    payload: dict[str, object]


class ApprovalDecisionRequest(ContractModel):
    decision: ApprovalStatus
    approver: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def must_be_final_decision(self) -> ApprovalDecisionRequest:
        if self.decision == ApprovalStatus.PENDING:
            raise ValueError("decision must be approved or rejected")
        return self


class RouteApplyRequest(ContractModel):
    approval_id: str = Field(min_length=1)


class RouteApplyReceipt(ContractModel):
    approval_id: str
    destination_bank: str
    from_gateway: str
    to_gateway: str
    status: str = "applied"
    simulated_only: bool = True


class DemoInjectRequest(ContractModel):
    transactions: int = Field(default=1284, ge=1, le=2000)
    customers: int = Field(default=1109, ge=1, le=2000)
    complaint_count: int = Field(default=13, ge=0, le=2000)
    retry_customers: int = Field(default=27, ge=0, le=2000)
    history_refresh_customers: int = Field(default=120, ge=0, le=2000)
    channel_switch_customers: int = Field(default=18, ge=0, le=2000)

    @model_validator(mode="after")
    def counts_fit_customer_population(self) -> DemoInjectRequest:
        if self.transactions < self.customers:
            raise ValueError("transactions must be at least the positive customer count")
        if self.complaint_count > self.customers:
            raise ValueError("complaint_count cannot exceed customers")
        if max(self.retry_customers, self.history_refresh_customers, self.channel_switch_customers) > self.customers:
            raise ValueError("behaviour counts cannot exceed customers")
        return self


class TwinSimulator:
    """Runs before/after traffic against detached copies of the live simulator."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank

    def simulate(self, request: TwinSimulationRequest) -> TwinSimulationReceipt:
        destination = request.destination_bank
        current_gateway = self.bank.payment_router.route_for(destination)
        baseline = self._clone_bank()
        candidate = self._clone_bank()
        candidate.payment_router.routes[destination] = request.to_gateway

        baseline_results = self._run_batch(baseline, destination, current_gateway, request.runs, "twin-before")
        candidate_results = self._run_batch(candidate, destination, request.to_gateway, request.runs, "twin-after")
        baseline_failures = sum(item.status != TransferStatus.COMPLETED for item in baseline_results)
        candidate_failures = sum(item.status != TransferStatus.COMPLETED for item in candidate_results)
        candidate_events = [
            event for event in candidate.events
            if event.payload.get("transaction_id") == candidate_results[0].transaction_id
        ]
        trace = [
            TraceStep(
                service=event.service,
                result=event.code or "EVENT",
                latency_ms=event.latency_ms,
                error_code=event.code if event.code and "TIMEOUT" in event.code else None,
            )
            for event in candidate_events
        ]
        return TwinSimulationReceipt(
            destination_bank=destination,
            from_gateway=current_gateway,
            to_gateway=request.to_gateway,
            runs=request.runs,
            baseline_completed=request.runs - baseline_failures,
            candidate_completed=request.runs - candidate_failures,
            baseline_failures=baseline_failures,
            candidate_failures=candidate_failures,
            improvement=baseline_failures - candidate_failures,
            trace=trace,
        )

    def _clone_bank(self) -> SimulatedBank:
        twin = SimulatedBank()
        twin.payment_router.routes = dict(self.bank.payment_router.routes)
        twin.payment_router.acknowledgement_failures = set(
            self.bank.payment_router.acknowledgement_failures
        )
        for name, destination in self.bank.destination_banks.items():
            twin.destination_banks[name].available = destination.available
        twin.current_app_version = self.bank.current_app_version
        return twin

    @staticmethod
    def _run_batch(
        bank: SimulatedBank, destination: str, gateway: str, runs: int, prefix: str
    ) -> list[TransferResult]:
        bank.payment_router.routes[destination] = gateway
        return [
            bank.transfer(
                transaction_id=f"{prefix}-{index + 1:02d}",
                customer_token=f"cust_{prefix}_{index + 1:02d}",
                destination_bank=destination,
                amount_ngn=Decimal("1000"),
                channel="mobile",
            )
            for index in range(runs)
        ]


class DemoController:
    """Owns repeatable demo lifecycle state; all banking remains in memory."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank
        self.failure_injector = FailureInjector(bank)
        self.traffic = TrafficGenerator(bank)
        self.started = False
        self.injected = False
        self.last_injection: dict[str, object] | None = None

    def _initialize(self, status: str) -> dict[str, object]:
        self.failure_injector.reset(start_at=datetime.now(timezone.utc))
        self.traffic.reset()
        baseline = self.traffic.generate_healthy_baseline(transactions=30)
        self.started = True
        self.injected = False
        self.last_injection = None
        return {
            "status": status,
            "baseline_transfers": len(baseline),
            "event_count": len(self.bank.events),
            "bank_x_route": self.bank.payment_router.route_for("BANK_X"),
            "failure_active": False,
            "simulated_only": True,
        }

    def start(self) -> dict[str, object]:
        return self._initialize("started")

    def reset(self) -> dict[str, object]:
        return self._initialize("reset")

    def inject(self, request: DemoInjectRequest) -> dict[str, object]:
        if not self.started:
            raise RuntimeError("start or reset the demo before injecting an incident")
        if self.injected and self.last_injection is not None:
            return self.last_injection

        fault = self.failure_injector.inject()
        summary: TrafficSummary = self.traffic.generate_incident_traffic(
            transactions=request.transactions,
            customers=request.customers,
            complaint_count=request.complaint_count,
            retry_customers=request.retry_customers,
            history_refresh_customers=request.history_refresh_customers,
            channel_switch_customers=request.channel_switch_customers,
        )
        receipt: dict[str, object] = {
            "status": "injected",
            "scenario": "gateway_b_acknowledgement_failure_with_app_release_decoy",
            "fault_gateway": fault.gateway,
            "destination_bank": fault.destination_bank,
            "decoy_app_version": fault.app_version,
            "transactions": summary.transactions_generated,
            "customers": summary.unique_customers,
            "complaints": summary.complaint_count,
            "silent_customers": summary.silent_customer_count,
            "acknowledgement_timeouts": summary.acknowledgement_timeouts,
            "idempotent_retries": summary.idempotent_retries,
            "history_refreshes": summary.history_refreshes,
            "channel_switches": summary.channel_switches,
            "exposure_ngn": str(summary.exposure_ngn),
            "incident_event_count": summary.event_count + 2,
            "total_simulated_event_count": len(self.bank.events),
            "simulated_only": True,
        }
        self.injected = True
        self.last_injection = receipt
        return receipt


class ControlPlane:
    """Coordinates approvals, twin evaluation, safe config application, and demo reset."""

    def __init__(
        self,
        bank: SimulatedBank,
        event_stream: EventStreamService,
        ghost_test_agent: GhostTestAgent,
        flush_simulator_events,
    ) -> None:
        self.bank = bank
        self.event_stream = event_stream
        self.ghost_test_agent = ghost_test_agent
        self.flush_simulator_events = flush_simulator_events
        self.twin = TwinSimulator(bank)
        self.demo = DemoController(bank)
        self.approvals: dict[str, Approval] = {}
        self.applied_routes: dict[str, RouteApplyReceipt] = {}
        self._lock = asyncio.Lock()

    async def create_approval(self, request: ApprovalCreateRequest) -> Approval:
        async with self._lock:
            approval = Approval(
                id=f"apr-{uuid4().hex[:12]}",
                level=request.level,
                kind=request.kind,
                payload=request.payload,
                status=ApprovalStatus.PENDING,
            )
            self.approvals[approval.id] = approval
            await self._record_approval(approval)
            return approval

    async def decide_approval(self, approval_id: str, request: ApprovalDecisionRequest) -> Approval:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            if approval.status != ApprovalStatus.PENDING:
                raise ValueError(f"approval {approval_id} has already been decided")
            decided = approval.model_copy(update={
                "status": request.decision,
                "approver": request.approver,
                "decided_at": datetime.now(timezone.utc),
            })
            # Revalidate after model_copy because Pydantic does not validate update values.
            decided = Approval.model_validate(decided.model_dump())
            self.approvals[approval_id] = decided
            await self._record_approval(decided)
            return decided

    async def apply_route(self, approval_id: str) -> RouteApplyReceipt:
        async with self._lock:
            if approval_id in self.applied_routes:
                return self.applied_routes[approval_id]
            approval = self.approvals.get(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            if approval.status != ApprovalStatus.APPROVED:
                raise ValueError("route changes require an approved config request")
            if approval.kind != ApprovalKind.CONFIG or approval.level != ApprovalLevel.ENGINEER:
                raise ValueError("route changes require an approved engineer config request")
            destination = approval.payload.get("destination_bank")
            gateway = approval.payload.get("to_gateway")
            action = approval.payload.get("action")
            if action != "reroute" or not isinstance(destination, str) or not isinstance(gateway, str):
                raise ValueError("approval payload must specify action=reroute, destination_bank, and to_gateway")
            previous = self.bank.apply_route(destination, gateway, approval=approval)
            await self.flush_simulator_events()
            resolution = ResolutionEvent(
                id=f"res-{uuid4().hex[:12]}",
                action=f"reroute {destination} from {previous} to {gateway}",
                status="applied_in_simulated_bank",
                verified_by_ghost_test=False,
            )
            await self.event_stream.ingest(
                "resolution_event",
                resolution.model_dump(mode="json"),
                occurred_at=datetime.now(timezone.utc),
            )
            receipt = RouteApplyReceipt(
                approval_id=approval.id,
                destination_bank=destination,
                from_gateway=previous,
                to_gateway=gateway,
            )
            self.applied_routes[approval_id] = receipt
            return receipt

    async def reset_demo(self) -> dict[str, object]:
        async with self._lock:
            receipt = self.demo.reset()
            await self.ghost_test_agent.reset()
            self.approvals.clear()
            self.applied_routes.clear()
            await self.flush_simulator_events()
            return receipt

    async def start_demo(self) -> dict[str, object]:
        async with self._lock:
            receipt = self.demo.start()
            await self.ghost_test_agent.reset()
            self.approvals.clear()
            self.applied_routes.clear()
            await self.flush_simulator_events()
            return receipt

    async def inject_demo(self, request: DemoInjectRequest) -> dict[str, object]:
        async with self._lock:
            receipt = self.demo.inject(request)
            await self.flush_simulator_events()
            return receipt

    async def _record_approval(self, approval: Approval) -> None:
        await self.event_stream.ingest(
            "approval",
            approval.model_dump(mode="json"),
            occurred_at=datetime.now(timezone.utc),
        )


__all__ = [
    "ApprovalCreateRequest",
    "ApprovalDecisionRequest",
    "ControlPlane",
    "DemoInjectRequest",
    "RouteApplyReceipt",
    "RouteApplyRequest",
    "TwinSimulationReceipt",
    "TwinSimulationRequest",
    "TwinSimulator",
]
