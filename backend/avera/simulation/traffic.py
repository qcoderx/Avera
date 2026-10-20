"""Repeatable synthetic traffic and messy customer complaint generation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from avera.contracts import Complaint
from avera.simulation.bank import SimulatedBank, TransferResult, TransferStatus


MESSY_COMPLAINTS = (
    "abeg my acct don debit since, the other bank no see am",
    "Transfer is hanging and money has left my account pls check",
    "I was debited but the recipient says nothing came in??",
    "txn failed on app but debit alert entered. What happened",
    "Bank X transfer still pending since morning, help please",
    "I tried again and now I may have been charged twice",
    "money comot my balance but beneficiary never get am",
    "Please confirm if this payment went through, it is still loading",
    "I used USSD after the app and both show pending",
    "Debit alert received. Destination bank has no record of transfer",
    "My transfer failed but my balance reduced. Ref txn please",
    "The recipient has not seen the funds and I have been debited",
    "Can someone check this transfer for me? Still waiting",
)


@dataclass(frozen=True, slots=True)
class TrafficSummary:
    transactions_generated: int
    unique_customers: int
    complaint_count: int
    silent_customer_count: int
    acknowledgement_timeouts: int
    idempotent_retries: int
    history_refreshes: int
    channel_switches: int
    exposure_ngn: Decimal
    event_count: int
    customer_tokens: tuple[str, ...]
    complaints: tuple[Complaint, ...]
    transactions: tuple[TransferResult, ...]


class ComplaintGenerator:
    """Produces intentionally unpolished complaints; parsing belongs to Shaz."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank
        self._number = 0

    def generate(
        self,
        customer_tokens: list[str] | tuple[str, ...],
        *,
        count: int = 13,
    ) -> list[Complaint]:
        if count < 0 or count > len(customer_tokens):
            raise ValueError("complaint count must be between zero and the customer count")
        generated: list[Complaint] = []
        for index in range(count):
            self._number += 1
            complaint = Complaint(
                id=f"cmp-demo-{self._number:03d}",
                ts=self.bank.clock.now(),
                channel="ussd" if index % 4 == 3 else "mobile",
                raw_text=MESSY_COMPLAINTS[index % len(MESSY_COMPLAINTS)],
                customer_token=customer_tokens[index],
                parsed=None,
            )
            self.bank.record_complaint(complaint)
            generated.append(complaint)
        return generated

    def reset(self) -> None:
        self._number = 0


class TrafficGenerator:
    """Creates baseline traffic and an incident-sized Bank X traffic burst."""

    def __init__(self, bank: SimulatedBank) -> None:
        self.bank = bank
        self.complaints = ComplaintGenerator(bank)

    def generate_healthy_baseline(self, *, transactions: int = 30) -> list[TransferResult]:
        """Generate deterministic healthy transfers over Bank Y and Bank Z."""
        if transactions < 0:
            raise ValueError("transactions cannot be negative")
        generated = []
        banks = ("BANK_Y", "BANK_Z")
        channels = ("mobile", "ussd", "internet_banking")
        for index in range(transactions):
            transaction_id = f"txn-healthy-{index + 1:06d}"
            generated.append(
                self.bank.transfer(
                    transaction_id=transaction_id,
                    customer_token=f"cust_healthy_{index % 100 + 1:04d}",
                    destination_bank=banks[index % len(banks)],
                    amount_ngn=Decimal("5000") + Decimal((index % 8) * 250),
                    channel=channels[index % len(channels)],
                    app_version="4.1",
                    idempotency_key=transaction_id,
                )
            )
        return generated

    def generate_incident_traffic(
        self,
        *,
        transactions: int = 1284,
        customers: int = 1109,
        complaint_count: int = 13,
        retry_customers: int = 27,
        history_refresh_customers: int = 120,
        channel_switch_customers: int = 18,
    ) -> TrafficSummary:
        """Generate the seeded Gateway B incident and its silent behaviours."""
        if customers < 1 or transactions < customers:
            raise ValueError("transactions must be at least the positive customer count")
        if complaint_count < 0 or complaint_count > customers:
            raise ValueError("complaint_count must be between zero and customers")
        if retry_customers < 0 or history_refresh_customers < 0 or channel_switch_customers < 0:
            raise ValueError("behaviour counts cannot be negative")
        if max(retry_customers, history_refresh_customers, channel_switch_customers) > customers:
            raise ValueError("behaviour counts cannot exceed the customer count")

        initial_event_count = len(self.bank.events)
        customer_tokens = tuple(f"cust_demo_{index + 1:04d}" for index in range(customers))
        results: list[TransferResult] = []
        exposure = Decimal("0")
        retries = 0
        timeouts = 0

        for index in range(transactions):
            transaction_id = f"txn-demo-{index + 1:06d}"
            customer_token = customer_tokens[index % customers]
            amount = Decimal("12000") + Decimal((index % 5) * 500)
            channel = "ussd" if index % 5 == 4 else "mobile"
            result = self.bank.transfer(
                transaction_id=transaction_id,
                customer_token=customer_token,
                destination_bank="BANK_X",
                amount_ngn=amount,
                channel=channel,
                idempotency_key=transaction_id,
            )
            results.append(result)
            exposure += amount
            if result.status == TransferStatus.PENDING:
                timeouts += 1

            if index < retry_customers:
                self.bank.transfer(
                    transaction_id=transaction_id,
                    customer_token=customer_token,
                    destination_bank="BANK_X",
                    amount_ngn=amount,
                    channel=channel,
                    idempotency_key=transaction_id,
                )
                retries += 1
                self.bank.emit_event(
                    source="behavior-telemetry",
                    channel=channel,
                    service="transfer-ui",
                    route="transfer.retry",
                    code="RETRY_LOOP",
                    customer_token=customer_token,
                    payload={"transaction_id": transaction_id, "retry_count": 2, "window_seconds": 60},
                )

        for index in range(history_refresh_customers):
            customer_token = customer_tokens[index]
            for refresh in range(2):
                self.bank.api.refresh_history(customer_token)
            self.bank.emit_event(
                source="behavior-telemetry",
                channel="mobile",
                service="transaction-history",
                route="history.refresh",
                code="HISTORY_REFRESH_SPIKE",
                customer_token=customer_token,
                payload={"refresh_count": 2, "window_seconds": 60},
            )

        for index in range(channel_switch_customers):
            customer_token = customer_tokens[index]
            self.bank.emit_event(
                source="behavior-telemetry",
                channel="mobile",
                service="mobile-app",
                route="mobile.to.ussd",
                code="CHANNEL_SWITCH",
                customer_token=customer_token,
                payload={"from_channel": "mobile", "to_channel": "ussd"},
            )

        complaints = self.complaints.generate(customer_tokens, count=complaint_count)
        return TrafficSummary(
            transactions_generated=len(results),
            unique_customers=customers,
            complaint_count=len(complaints),
            silent_customer_count=customers - len(complaints),
            acknowledgement_timeouts=timeouts,
            idempotent_retries=retries,
            history_refreshes=history_refresh_customers * 2,
            channel_switches=channel_switch_customers,
            exposure_ngn=exposure,
            event_count=len(self.bank.events) - initial_event_count,
            customer_tokens=customer_tokens,
            complaints=tuple(complaints),
            transactions=tuple(results),
        )

    def reset(self) -> None:
        self.complaints.reset()
