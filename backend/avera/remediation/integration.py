"""In-process adapters that join observed simulator events to the remediation workflow.

These adapters are for the single-bank MVP only. They deliberately keep the bank
simulated and expose no real-bank routing, payment, or customer-contact credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from avera.contracts import Complaint, ContractModel, ContractObjectType, Event, TestVerdict
from avera.events.service import EventStreamService
from avera.observability.orchestration import ObservabilityAgent
from avera.remediation.blast_radius import BlastRadiusConfig
from avera.remediation.common import naira_to_kobo
from avera.remediation.fixtures import DISPLAY_TZ, DemoWorld, SimulatedBank as RemediationBank, build_demo_world
from avera.remediation.models import BehaviourSignal, Role, RouteChange, TransactionRecord, ValidatedHandoff
from avera.remediation.pipeline import Remediation, build_remediation
from avera.remediation.ports import BankData, Clock, EventPublisher

FAILURE_CODES = {"ACK_TIMEOUT", "NO_CREDIT", "CREDIT_REJECTED"}
BEHAVIOUR_CODES = {
    "IDEMPOTENT_RETRY": "retry",
    "HISTORY_REFRESH": "history_refresh",
    "CHANNEL_SWITCH": "channel_switch",
    "ABANDONED": "abandonment",
}


class SystemClock(Clock):
    def __init__(self, observed: ObservedBankData) -> None:
        self.observed = observed

    def now(self) -> datetime:
        wall_clock = datetime.now(timezone.utc)
        latest = self.observed.latest_at
        return max(wall_clock, latest + timedelta(microseconds=1)) if latest else wall_clock


class ObservedBankData(BankData):
    """Read-only impact inputs derived from the canonical observed event window."""

    def __init__(self) -> None:
        self.transactions: list[TransactionRecord] = []
        self.complaints: list[Complaint] = []
        self.behaviours: list[BehaviourSignal] = []
        self.latest_at: datetime | None = None

    def refresh(self, agent: ObservabilityAgent) -> None:
        events = [item for item in agent.items.values() if isinstance(item, Event)]
        timestamps = [item.ts for item in agent.items.values()]
        self.latest_at = max(timestamps) if timestamps else None
        grouped: dict[str, list[Event]] = {}
        for event in events:
            txn_id = event.payload.get("transaction_id")
            if isinstance(txn_id, str) and txn_id:
                grouped.setdefault(txn_id, []).append(event)

        transactions = []
        for txn_id, trace in grouped.items():
            requests = [event for event in trace if event.code == "TRANSFER_REQUESTED"]
            terminals = [event for event in trace if event.code in FAILURE_CODES | {"ACK_RECEIVED"}]
            if not requests or not terminals:
                continue
            request = min(requests, key=lambda event: (event.ts, event.id))
            terminal = max(terminals, key=lambda event: (event.ts, event.id))
            token = request.customer_token or terminal.customer_token
            amount = request.payload.get("amount_ngn")
            destination = terminal.payload.get("destination_bank") or request.payload.get("destination_bank")
            if not token or amount is None or not destination or not terminal.route:
                continue
            try:
                amount_kobo = naira_to_kobo(Decimal(str(amount)))
            except Exception:
                continue
            codes = {event.code for event in trace}
            credit_posted = "CREDIT_ACCEPTED" in codes
            transactions.append(TransactionRecord(
                txn_id=txn_id,
                customer_token=token,
                ts=terminal.ts,
                amount_kobo=amount_kobo,
                channel=request.channel,
                product="bank_transfer",
                route=terminal.route,
                destination_bank=str(destination),
                error_code=terminal.code if terminal.code in FAILURE_CODES else None,
                app_version=request.payload.get("app_version"),
                debit_status="posted" if "DEBIT_POSTED" in codes else "unknown",
                credit_status="confirmed" if credit_posted and terminal.code == "ACK_RECEIVED"
                else "unknown" if credit_posted else "rejected",
            ))
        self.transactions = transactions

        self.complaints = [
            agent.parsed.get(item.id, item)
            for item in agent.items.values() if isinstance(item, Complaint)
        ]
        self.behaviours = [
            BehaviourSignal(
                ts=event.ts,
                customer_token=event.customer_token,
                kind=BEHAVIOUR_CODES[event.code],
                txn_id=event.payload.get("transaction_id"),
            )
            for event in events
            if event.code in BEHAVIOUR_CODES and event.customer_token
        ]

    def clear(self) -> None:
        self.transactions.clear()
        self.complaints.clear()
        self.behaviours.clear()
        self.latest_at = None

    def find_transactions(self, traits: dict[str, str], start: datetime, end: datetime) -> list[TransactionRecord]:
        return [item for item in self.transactions if start <= item.ts <= end
                and all(item.trait(key) == value for key, value in traits.items())]

    def complaints_between(self, start: datetime, end: datetime) -> list[Complaint]:
        return [item for item in self.complaints if start <= item.ts <= end]

    def behaviour_signals_between(self, start: datetime, end: datetime) -> list[BehaviourSignal]:
        return [item for item in self.behaviours if start <= item.ts <= end]


class BufferedEventPublisher(EventPublisher):
    """Bridge the synchronous remediation domain to async canonical event ingestion."""

    def __init__(self) -> None:
        self.pending: list[tuple[ContractObjectType, ContractModel]] = []

    def publish(self, object_type: ContractObjectType, data: ContractModel) -> None:
        self.pending.append((object_type, data))

    async def flush(self, stream: EventStreamService) -> None:
        while self.pending:
            object_type, data = self.pending[0]
            await stream.ingest(object_type, data.model_dump(mode="json"))
            del self.pending[0]


class CoordinatedSimulatedRouting:
    """Apply a human-approved route to both simulator views; never targets a live bank."""

    def __init__(self, api_bank, remediation_bank: RemediationBank) -> None:
        self.api_bank = api_bank
        self.remediation_bank = remediation_bank

    def apply_route(self, change) -> str:
        destination = change.destination
        from_gateway = change.from_route.split(".", 1)[0]
        to_gateway = change.to_route.split(".", 1)[0]
        if (self.api_bank.payment_router.route_for(destination) != from_gateway
                or self.remediation_bank.routes.get(destination) != change.from_route):
            raise RuntimeError("simulated route changed since the approved plan was created")
        change_id = self.remediation_bank.apply_route(change)
        self.api_bank.payment_router.apply_route(destination, to_gateway)
        return change_id


@dataclass
class RemediationRuntime:
    world: DemoWorld
    remediation: Remediation
    bank_data: ObservedBankData
    publisher: BufferedEventPublisher
    api_bank: object

    async def flush_events(self, stream: EventStreamService) -> None:
        await self.publisher.flush(stream)

    def snapshot(self, active_case_id: str | None) -> dict:
        return {
            "version": 1,
            "active_case_id": active_case_id,
            "approvals": self.remediation.approvals.snapshot(),
            "audit": [entry.model_dump(mode="json") for entry in self.remediation.audit.entries()],
            "cases": self.remediation.store.snapshot(),
            "routes": dict(self.world.bank.routes),
            "api_routes": dict(self.api_bank.payment_router.routes),
            "applied_changes": [item.model_dump(mode="json") for item in self.world.bank.applied],
        }

    def restore(self, snapshot: dict) -> str | None:
        if snapshot.get("version") != 1:
            raise ValueError("unsupported remediation state snapshot version")
        self.remediation.approvals.restore(snapshot.get("approvals", []))
        self.remediation.audit.restore(snapshot.get("audit", []))
        self.remediation.store.restore(snapshot.get("cases", []))

        routes = snapshot.get("routes", {})
        if not isinstance(routes, dict) or not all(
            isinstance(destination, str) and isinstance(route, str) and "." in route
            for destination, route in routes.items()
        ):
            raise ValueError("persisted remediation routes are invalid")
        self.world.bank.routes = dict(routes)
        self.world.bank.applied = [
            RouteChange.model_validate(item) for item in snapshot.get("applied_changes", [])
        ]
        api_routes = snapshot.get("api_routes", {})
        if not isinstance(api_routes, dict) or not all(
            isinstance(destination, str) and isinstance(route, str)
            for destination, route in api_routes.items()
        ):
            raise ValueError("persisted API simulator routes are invalid")
        self.api_bank.payment_router.routes = dict(api_routes)

        active_case_id = snapshot.get("active_case_id")
        if active_case_id is not None and self.remediation.store.get(active_case_id) is None:
            raise ValueError("persisted active remediation case is missing")
        return active_case_id

    def make_handoff(self, agent: ObservabilityAgent) -> ValidatedHandoff | None:
        feedback = agent.feedback
        incident = agent.incident
        if feedback is None or incident is None:
            return None
        eligible = [
            item for item in feedback.evaluations
            if item.hypothesis.id in feedback.validated_hypothesis_ids
            and item.test_result.verdict == TestVerdict.VALIDATED
            and item.test_result.confidence >= feedback.handoff_threshold
        ]
        if not eligible:
            return None
        winner = min(eligible, key=lambda item: (-item.test_result.confidence, item.hypothesis.prior_rank))

        by_id = {item.id: item for item in agent.items.values() if isinstance(item, Event)}
        support = [by_id[ref] for ref in winner.hypothesis.evidence_for if ref in by_id]
        failed = [item for item in support if item.code in FAILURE_CODES and item.route]
        scopes = {(item.route, item.payload.get("destination_bank"), item.code) for item in failed}
        if len(scopes) != 1 or not support:
            return None
        route, destination, error_code = next(iter(scopes))
        if not all(isinstance(value, str) and value for value in (route, destination, error_code)):
            return None
        control_route = winner.test_result.control.route if winner.test_result.control else None
        if (not control_route or not control_route.endswith("." + destination.casefold())
                or control_route == route):
            return None
        gateway = route.split(".", 1)[0]
        request_txns = {
            item.payload.get("transaction_id") for item in support
            if item.code in FAILURE_CODES and item.payload.get("transaction_id")
        }
        if not any(event.code == "TRANSFER_REQUESTED"
                   and event.payload.get("transaction_id") in request_txns for event in agent.items.values()
                   if isinstance(event, Event)):
            return None

        fingerprint = {
            **incident.fingerprint,
            "route": route,
            "destination_bank": destination,
            "error_code": error_code,
            "product": "bank_transfer",
        }
        enriched_incident = incident.model_copy(update={"fingerprint": fingerprint})
        hypothesis = winner.hypothesis
        # The event topology names a gateway, while the remediation playbook uses
        # its explicit gateway/destination connector identifier.
        if hypothesis.zone == gateway:
            hypothesis = hypothesis.model_copy(update={"zone": f"{gateway}.{destination.casefold()}_connector"})
        self.bank_data.refresh(agent)
        return ValidatedHandoff(
            incident=enriched_incident,
            hypothesis=hypothesis,
            test_result=winner.test_result,
            rivals=[item.test_result for item in feedback.evaluations if item is not winner],
        )


def build_runtime(api_bank) -> RemediationRuntime:
    world = build_demo_world()
    bank_data = ObservedBankData()
    publisher = BufferedEventPublisher()
    ports = replace(
        world.ports,
        clock=SystemClock(bank_data),
        bank=bank_data,
        routing=CoordinatedSimulatedRouting(api_bank, world.bank),
        events=publisher,
    )
    remediation = build_remediation(ports, BlastRadiusConfig(display_tz=DISPLAY_TZ))
    return RemediationRuntime(
        world=world,
        remediation=remediation,
        bank_data=bank_data,
        publisher=publisher,
        api_bank=api_bank,
    )
