"""Deterministic in-memory digital bank used for the Avera demo.

All ledger mutations and messages in this module are local simulation state.
No external payment, bank, or SMS service is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Callable

from avera.contracts import (
    Approval,
    ApprovalKind,
    ApprovalLevel,
    ApprovalStatus,
    Complaint,
    Event,
)


DESTINATION_BANKS = ("BANK_X", "BANK_Y", "BANK_Z")
GATEWAYS = ("gateway_a", "gateway_b", "gateway_c")
CHANNELS = ("mobile", "ussd", "internet_banking", "atm", "pos")


class SimulationInputError(ValueError):
    """Raised when a demo request refers to an unsupported bank or route."""


class TransferStatus(StrEnum):
    COMPLETED = "completed"
    PENDING = "pending"
    DEBIT_WITHOUT_CREDIT = "debit_without_credit"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TransferResult:
    transaction_id: str
    customer_token: str
    destination_bank: str
    amount_ngn: Decimal
    channel: str
    app_version: str
    gateway: str
    status: TransferStatus
    debit_posted: bool
    destination_credit_posted: bool
    acknowledged: bool
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SMSDelivery:
    message_id: str
    recipient_token: str
    template_id: str
    message: str
    status: str


@dataclass(frozen=True, slots=True)
class OTPChallenge:
    challenge_id: str
    customer_token: str
    code: str
    expires_at: datetime


class SimulationClock:
    """A controllable clock with stable, increasing timestamps for each run."""

    def __init__(self, start_at: datetime | None = None) -> None:
        initial = start_at or datetime.now(timezone.utc)
        if initial.tzinfo is None or initial.utcoffset() is None:
            raise ValueError("simulation clock start_at must include a timezone")
        self._initial = initial.astimezone(timezone.utc)
        self._current = self._initial

    def now(self) -> datetime:
        current = self._current
        self._current += timedelta(milliseconds=1)
        return current

    def reset(self) -> None:
        self._current = self._initial


class CoreBankingSimulator:
    """A tiny in-memory debit ledger with transaction-level idempotency."""

    def __init__(self, starting_balance: Decimal = Decimal("1000000")) -> None:
        if starting_balance < 0:
            raise ValueError("starting_balance cannot be negative")
        self.starting_balance = starting_balance
        self.balances: dict[str, Decimal] = {}
        self.debits: dict[str, tuple[str, Decimal]] = {}

    def debit(self, customer_token: str, transaction_id: str, amount: Decimal) -> tuple[bool, bool]:
        """Return (accepted, newly_posted); repeated transaction IDs never debit twice."""
        if transaction_id in self.debits:
            return True, False
        balance = self.balances.get(customer_token, self.starting_balance)
        if balance < amount:
            return False, False
        self.balances[customer_token] = balance - amount
        self.debits[transaction_id] = (customer_token, amount)
        return True, True

    def reset(self) -> None:
        self.balances.clear()
        self.debits.clear()


class DestinationBankSimulator:
    """A destination bank ledger; credits are idempotent by transaction ID."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.available = True
        self.credits: dict[str, tuple[str, Decimal]] = {}

    def credit(self, customer_token: str, transaction_id: str, amount: Decimal) -> bool:
        if not self.available:
            return False
        self.credits.setdefault(transaction_id, (customer_token, amount))
        return True

    def reset(self) -> None:
        self.available = True
        self.credits.clear()


class PaymentRouterSimulator:
    """Routes transfers to gateways and can suppress selected acknowledgements."""

    DEFAULT_ROUTES = {"BANK_X": "gateway_b", "BANK_Y": "gateway_a", "BANK_Z": "gateway_c"}

    def __init__(self) -> None:
        self.routes = dict(self.DEFAULT_ROUTES)
        self.acknowledgement_failures: set[tuple[str, str]] = set()

    def route_for(self, destination_bank: str) -> str:
        try:
            return self.routes[destination_bank]
        except KeyError as exc:
            raise SimulationInputError(f"unsupported destination bank: {destination_bank}") from exc

    def acknowledgement_received(self, gateway: str, destination_bank: str) -> bool:
        return (gateway, destination_bank) not in self.acknowledgement_failures

    def set_acknowledgement_failure(
        self, gateway: str, destination_bank: str, enabled: bool = True
    ) -> None:
        if gateway not in GATEWAYS:
            raise SimulationInputError(f"unsupported gateway: {gateway}")
        if destination_bank not in DESTINATION_BANKS:
            raise SimulationInputError(f"unsupported destination bank: {destination_bank}")
        key = (gateway, destination_bank)
        if enabled:
            self.acknowledgement_failures.add(key)
        else:
            self.acknowledgement_failures.discard(key)

    def apply_route(self, destination_bank: str, gateway: str) -> str:
        if destination_bank not in DESTINATION_BANKS:
            raise SimulationInputError(f"unsupported destination bank: {destination_bank}")
        if gateway not in GATEWAYS:
            raise SimulationInputError(f"unsupported gateway: {gateway}")
        previous = self.routes[destination_bank]
        self.routes[destination_bank] = gateway
        return previous

    def reset(self) -> None:
        self.routes = dict(self.DEFAULT_ROUTES)
        self.acknowledgement_failures.clear()


class SimulatedBank:
    """Coordinates simulated bank services and emits shared contract events."""

    def __init__(
        self,
        *,
        start_at: datetime | None = None,
        starting_balance: Decimal = Decimal("1000000"),
        event_sink: Callable[[Event], None] | None = None,
        complaint_sink: Callable[[Complaint], None] | None = None,
    ) -> None:
        self.clock = SimulationClock(start_at)
        self._event_sink = event_sink
        self._complaint_sink = complaint_sink
        self.core_banking = CoreBankingSimulator(starting_balance)
        self.payment_router = PaymentRouterSimulator()
        self.destination_banks = {
            name: DestinationBankSimulator(name) for name in DESTINATION_BANKS
        }
        self.events: list[Event] = []
        self.complaints: list[Complaint] = []
        self.transfers: dict[str, TransferResult] = {}
        self._idempotency: dict[str, str] = {}
        self._event_number = 0
        self.current_app_version = "4.1"
        self.api = CustomerAppAPI(self)
        self.sms_gateway = SimulatedSMSGateway(self)
        self.auth_otp = AuthOTPService(self, self.sms_gateway)

    def emit_event(
        self,
        *,
        source: str,
        channel: str,
        service: str,
        route: str | None,
        code: str,
        customer_token: str | None = None,
        latency_ms: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> Event:
        self._event_number += 1
        event = Event(
            id=f"evt-sim-{self._event_number:06d}",
            ts=self.clock.now(),
            source=source,
            channel=channel,
            service=service,
            route=route,
            code=code,
            customer_token=customer_token,
            latency_ms=latency_ms,
            payload=payload or {},
        )
        self.events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
        return event

    def record_complaint(self, complaint: Complaint) -> None:
        self.complaints.append(complaint)
        if self._complaint_sink is not None:
            self._complaint_sink(complaint)

    def set_event_sinks(
        self,
        *,
        event_sink: Callable[[Event], None] | None,
        complaint_sink: Callable[[Complaint], None] | None,
    ) -> None:
        """Connect simulator output to an ingestion pipeline without changing its state."""
        self._event_sink = event_sink
        self._complaint_sink = complaint_sink

    def transfer(
        self,
        *,
        transaction_id: str,
        customer_token: str,
        destination_bank: str,
        amount_ngn: Decimal | int | str,
        channel: str,
        app_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> TransferResult:
        """Execute a simulated transfer and emit its service-by-service evidence."""
        if not transaction_id or not customer_token:
            raise SimulationInputError("transaction_id and customer_token are required")
        if channel not in CHANNELS:
            raise SimulationInputError(f"unsupported channel: {channel}")
        if destination_bank not in DESTINATION_BANKS:
            raise SimulationInputError(f"unsupported destination bank: {destination_bank}")
        amount = Decimal(str(amount_ngn))
        if not amount.is_finite() or amount <= 0:
            raise SimulationInputError("amount_ngn must be a finite positive amount")

        key = idempotency_key or transaction_id
        if key in self._idempotency:
            original_id = self._idempotency[key]
            original = self.transfers[original_id]
            self.emit_event(
                source="api-gateway",
                channel=channel,
                service="payment-router",
                route=f"transfer.retry.{original.gateway}",
                code="IDEMPOTENT_RETRY",
                customer_token=customer_token,
                payload={"transaction_id": original_id, "idempotency_key": key},
            )
            return original

        selected_app_version = app_version or self.current_app_version
        gateway = self.payment_router.route_for(destination_bank)
        route = f"{gateway}.{destination_bank.lower()}"
        self.emit_event(
            source="customer-app",
            channel=channel,
            service="api-gateway",
            route="transfers.create",
            code="TRANSFER_REQUESTED",
            customer_token=customer_token,
            payload={
                "transaction_id": transaction_id,
                "destination_bank": destination_bank,
                "amount_ngn": str(amount),
                "app_version": selected_app_version,
            },
        )

        debited, _new_debit = self.core_banking.debit(customer_token, transaction_id, amount)
        self.emit_event(
            source="core-banking",
            channel=channel,
            service="core-banking",
            route="ledger.debit",
            code="DEBIT_POSTED" if debited else "INSUFFICIENT_FUNDS",
            customer_token=customer_token,
            payload={"transaction_id": transaction_id, "amount_ngn": str(amount)},
        )
        if not debited:
            result = TransferResult(
                transaction_id=transaction_id,
                customer_token=customer_token,
                destination_bank=destination_bank,
                amount_ngn=amount,
                channel=channel,
                app_version=selected_app_version,
                gateway=gateway,
                status=TransferStatus.FAILED,
                debit_posted=False,
                destination_credit_posted=False,
                acknowledged=False,
                idempotency_key=key,
            )
            self._remember_transfer(result)
            return result

        destination = self.destination_banks[destination_bank]
        credited = destination.credit(customer_token, transaction_id, amount)
        self.emit_event(
            source=f"destination-{destination_bank.lower()}",
            channel=channel,
            service=f"destination-bank-{destination_bank.lower()}",
            route=f"{destination_bank.lower()}.credit",
            code="CREDIT_ACCEPTED" if credited else "CREDIT_REJECTED",
            customer_token=customer_token,
            payload={"transaction_id": transaction_id, "amount_ngn": str(amount)},
        )

        acknowledged = credited and self.payment_router.acknowledgement_received(
            gateway, destination_bank
        )
        status = (
            TransferStatus.COMPLETED
            if acknowledged
            else TransferStatus.PENDING
            if credited
            else TransferStatus.DEBIT_WITHOUT_CREDIT
        )
        self.emit_event(
            source=gateway,
            channel=channel,
            service=gateway,
            route=route,
            code="ACK_RECEIVED" if acknowledged else "ACK_TIMEOUT" if credited else "NO_CREDIT",
            customer_token=customer_token,
            latency_ms=114 if acknowledged else 30_000,
            payload={
                "transaction_id": transaction_id,
                "destination_bank": destination_bank,
                "destination_credit_posted": credited,
                "customer_visible_status": status.value,
            },
        )
        result = TransferResult(
            transaction_id=transaction_id,
            customer_token=customer_token,
            destination_bank=destination_bank,
            amount_ngn=amount,
            channel=channel,
            app_version=selected_app_version,
            gateway=gateway,
            status=status,
            debit_posted=True,
            destination_credit_posted=credited,
            acknowledged=acknowledged,
            idempotency_key=key,
        )
        self._remember_transfer(result)
        return result

    def apply_route(self, destination_bank: str, gateway: str, *, approval: Approval) -> str:
        """Change a simulated route only with a matching, approved engineer decision."""
        if (
            approval.status != ApprovalStatus.APPROVED
            or approval.level != ApprovalLevel.ENGINEER
            or approval.kind != ApprovalKind.CONFIG
        ):
            raise SimulationInputError("route changes require an approved engineer config decision")
        if (
            approval.payload.get("action") != "reroute"
            or approval.payload.get("destination_bank") != destination_bank
            or approval.payload.get("to_gateway") != gateway
        ):
            raise SimulationInputError("approval payload does not match the requested route change")
        previous = self.payment_router.apply_route(destination_bank, gateway)
        self.emit_event(
            source="admin-api",
            channel="internal",
            service="payment-router",
            route="admin.route",
            code="ROUTE_CHANGED",
            payload={
                "destination_bank": destination_bank,
                "from_gateway": previous,
                "to_gateway": gateway,
                "approval_id": approval.id,
            },
        )
        return previous

    def service_map(self) -> dict[str, object]:
        services = [
            {"id": "customer-app", "type": "channel"},
            {"id": "ussd-channel", "type": "channel"},
            {"id": "api-gateway", "type": "gateway"},
            {"id": "auth-otp", "type": "identity"},
            {"id": "core-banking", "type": "ledger"},
            {"id": "payment-router", "type": "payments"},
            *({"id": gateway, "type": "payment-gateway"} for gateway in GATEWAYS),
            *(
                {"id": f"destination-bank-{bank.lower()}", "type": "destination-bank"}
                for bank in DESTINATION_BANKS
            ),
            {"id": "simulated-sms-gateway", "type": "messaging"},
        ]
        dependencies = [
            {"from": "customer-app", "to": "api-gateway"},
            {"from": "ussd-channel", "to": "api-gateway"},
            {"from": "api-gateway", "to": "auth-otp"},
            {"from": "api-gateway", "to": "core-banking"},
            {"from": "api-gateway", "to": "payment-router"},
            *({"from": "payment-router", "to": gateway} for gateway in GATEWAYS),
            *(
                {"from": self.payment_router.routes[bank], "to": f"destination-bank-{bank.lower()}"}
                for bank in DESTINATION_BANKS
            ),
            {"from": "auth-otp", "to": "simulated-sms-gateway"},
        ]
        return {
            "services": services,
            "dependencies": dependencies,
            "routes": dict(self.payment_router.routes),
        }

    def reset(self) -> None:
        self.clock.reset()
        self.core_banking.reset()
        self.payment_router.reset()
        for destination in self.destination_banks.values():
            destination.reset()
        self.events.clear()
        self.complaints.clear()
        self.transfers.clear()
        self._idempotency.clear()
        self._event_number = 0
        self.current_app_version = "4.1"
        self.sms_gateway.reset()
        self.auth_otp.reset()

    def _remember_transfer(self, result: TransferResult) -> None:
        self.transfers[result.transaction_id] = result
        self._idempotency[result.idempotency_key] = result.transaction_id


class CustomerAppAPI:
    """Small customer-channel facade over the simulated bank API gateway."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank

    def transfer(
        self,
        *,
        transaction_id: str,
        customer_token: str,
        destination_bank: str,
        amount_ngn: Decimal | int | str,
        app_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> TransferResult:
        return self.bank.transfer(
            transaction_id=transaction_id,
            customer_token=customer_token,
            destination_bank=destination_bank,
            amount_ngn=amount_ngn,
            channel="mobile",
            app_version=app_version,
            idempotency_key=idempotency_key,
        )

    def refresh_history(self, customer_token: str, *, channel: str = "mobile") -> Event:
        return self.bank.emit_event(
            source="customer-app",
            channel=channel,
            service="transaction-history",
            route="history.refresh",
            code="HISTORY_REFRESH",
            customer_token=customer_token,
        )


class SimulatedSMSGateway:
    """Local SMS sink that records messages and emits delivery events."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank
        self.deliveries: list[SMSDelivery] = []
        self._number = 0

    def send(self, recipient_token: str, message: str, *, template_id: str) -> SMSDelivery:
        self._number += 1
        delivery = SMSDelivery(
            message_id=f"sms-sim-{self._number:06d}",
            recipient_token=recipient_token,
            template_id=template_id,
            message=message,
            status="delivered",
        )
        self.deliveries.append(delivery)
        self.bank.emit_event(
            source="simulated-sms-gateway",
            channel="sms",
            service="simulated-sms-gateway",
            route="sms.send",
            code="SMS_DELIVERED",
            customer_token=recipient_token,
            payload={"message_id": delivery.message_id, "template_id": template_id},
        )
        return delivery

    def reset(self) -> None:
        self.deliveries.clear()
        self._number = 0


class AuthOTPService:
    """Synthetic OTP service backed only by the simulated SMS gateway."""

    def __init__(self, bank: SimulatedBank, sms_gateway: SimulatedSMSGateway) -> None:
        self.bank = bank
        self.sms_gateway = sms_gateway
        self._number = 0
        self._challenges: dict[str, OTPChallenge] = {}
        self._used: set[str] = set()

    def request_otp(self, customer_token: str) -> OTPChallenge:
        self._number += 1
        challenge_id = f"otp-sim-{self._number:06d}"
        code = f"{(self._number * 7919) % 1_000_000:06d}"
        challenge = OTPChallenge(
            challenge_id=challenge_id,
            customer_token=customer_token,
            code=code,
            expires_at=self.bank.clock.now() + timedelta(minutes=5),
        )
        self._challenges[challenge_id] = challenge
        self.bank.emit_event(
            source="auth-otp-service",
            channel="mobile",
            service="auth-otp",
            route="otp.request",
            code="OTP_REQUESTED",
            customer_token=customer_token,
            payload={"challenge_id": challenge_id},
        )
        self.sms_gateway.send(
            customer_token,
            f"Your simulated Avera sign-in code is {code}.",
            template_id="demo-otp",
        )
        return challenge

    def verify_otp(self, challenge_id: str, code: str) -> bool:
        challenge = self._challenges.get(challenge_id)
        valid = (
            challenge is not None
            and challenge_id not in self._used
            and self.bank.clock.now() <= challenge.expires_at
            and code == challenge.code
        )
        if valid:
            self._used.add(challenge_id)
        self.bank.emit_event(
            source="auth-otp-service",
            channel="mobile",
            service="auth-otp",
            route="otp.verify",
            code="OTP_VERIFIED" if valid else "OTP_REJECTED",
            customer_token=challenge.customer_token if challenge else None,
            payload={"challenge_id": challenge_id},
        )
        return valid

    def reset(self) -> None:
        self._number = 0
        self._challenges.clear()
        self._used.clear()
