"""A deterministic stand-in for Koded's live system, built on his recorded incident fixture.

The incident, hypotheses, Ghost Test results and the 13 complaints come straight from
`avera/fixtures/full_incident_stream.json`. The full transfer history is synthesised around them so the demo
fingerprint yields 1,284 affected transactions and 1,109 affected customers, plus noise that must not be counted.
Replace each piece with Koded's real adapter as it lands.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from avera.contracts import (
    Complaint,
    ContractModel,
    ContractObjectType,
    IncidentFixture,
    TestResult,
    TestRunPurpose,
    TestVerdict,
    TraceStep,
)
from avera.remediation.blast_radius.agent import incident_window_start
from avera.remediation.common import naira_to_kobo
from avera.remediation.models import (
    BehaviourSignal,
    RouteChange,
    RouteMetrics,
    SandboxReport,
    SimulationResult,
    TransactionRecord,
    ValidatedHandoff,
)
from avera.remediation.ports import Contact, PatchProposal, Ports

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "full_incident_stream.json"
SEED = 48291
AFFECTED_CUSTOMERS = 1109
CUSTOMERS_WITH_TWO_TXNS = 175
PLAN_TIME = datetime(2026, 10, 20, 10, 57, tzinfo=timezone.utc)

# Koded's fixture records the PRD's wall-clock story (10:42, 10:51) with a Z offset, so the demo renders in UTC.
DISPLAY_TZ = timezone.utc

DESTINATION = "BANK_X"
BROKEN_ROUTE = "gateway_b.bank_x"
HEALTHY_ROUTE = "gateway_c.bank_x"


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> datetime:
        self._now += timedelta(**delta)
        return self._now


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.records: list[tuple[ContractObjectType, ContractModel]] = []

    def publish(self, object_type: ContractObjectType, data: ContractModel) -> None:
        self.records.append((object_type, data))

    def of_type(self, object_type: ContractObjectType) -> list[dict]:
        return [d.model_dump(mode="json") for t, d in self.records if t == object_type]


class FixtureBankData:
    def __init__(self, transactions: list[TransactionRecord], complaints: list[Complaint],
                 signals: list[BehaviourSignal]) -> None:
        self.transactions = transactions
        self.complaints = complaints
        self.signals = signals

    def find_transactions(self, traits: dict[str, str], start: datetime, end: datetime) -> list[TransactionRecord]:
        # Deliberately ignores `start` so the agent's own window check is what decides.
        return [t for t in self.transactions if t.ts <= end and all(t.trait(k) == v for k, v in traits.items())]

    def complaints_between(self, start: datetime, end: datetime) -> list[Complaint]:
        return [c for c in self.complaints if start <= c.ts <= end]

    def behaviour_signals_between(self, start: datetime, end: datetime) -> list[BehaviourSignal]:
        margin = timedelta(minutes=30)
        return [s for s in self.signals if start - margin <= s.ts <= end + margin]


class RouteConflict(RuntimeError):
    pass


class SimulatedBank:
    """Routing table, twin, Ghost Test reruns and live metrics for the demo bank."""

    def __init__(self) -> None:
        self.routes: dict[str, str] = {DESTINATION: BROKEN_ROUTE, "BANK_Y": "gateway_b.bank_y", "BANK_Z": "gateway_a.bank_z"}
        self.broken: set[str] = {BROKEN_ROUTE}
        self.route_error_rates: dict[tuple[str, str], float] = {(DESTINATION, HEALTHY_ROUTE): 0.004}
        self.applied: list[RouteChange] = []
        self._reruns = 0

    def simulate_route(self, change: RouteChange) -> SimulationResult:
        rate = 0.0 if change.to_route in self.broken else 0.967
        return SimulationResult(change=change, samples=300, success_rate=rate, passed=rate >= 0.9)

    def apply_route(self, change: RouteChange) -> str:
        current = self.routes.get(change.destination)
        if current != change.from_route:
            raise RouteConflict(f"{change.destination} is routed via {current}, not {change.from_route}")
        self.routes[change.destination] = change.to_route
        self.applied.append(change)
        return f"route-change-{len(self.applied):03d}"

    def rerun(self, incident_id: str, hypothesis_ids: list[str]) -> list[TestResult]:
        route = self.routes[DESTINATION]
        healthy = route not in self.broken
        results = []
        for hypothesis_id in hypothesis_ids:
            self._reruns += 1
            results.append(TestResult(
                id=f"ghost-{hypothesis_id.lower()}-postfix-rerun-{self._reruns:03d}",
                hypothesis_id=hypothesis_id,
                purpose=TestRunPurpose.POST_FIX_RERUN,
                test_plan=f"Rerun the original five synthetic {DESTINATION} transfers via {route}.",
                runs=5,
                reproduced=0 if healthy else 5,
                trace=[
                    TraceStep(service="payment-router", result="OK", latency_ms=27),
                    TraceStep(service=route, result="ACK_RECEIVED" if healthy else "ACK_TIMEOUT",
                              latency_ms=114 if healthy else 30000, error_code=None if healthy else "ACK_TIMEOUT"),
                ],
                confidence=0.96,
                verdict=TestVerdict.VALIDATED,
                rerun_passed=healthy,
            ))
        return results

    def route_metrics(self, destination: str, route: str, window_minutes: int) -> RouteMetrics:
        attempts = 500
        rate = self.route_error_rates.get((destination, route), 0.0)
        return RouteMetrics(destination=destination, route=route, window_minutes=window_minutes,
                            attempts=attempts, errors=round(attempts * rate))

    def live_signals_normal(self, incident_id: str, signals: list[str]) -> dict[str, bool]:
        healthy = self.routes[DESTINATION] not in self.broken
        return {s: healthy for s in signals}


class FixtureSandbox:
    def run_patch(self, repository: str, target: str, diff: str) -> SandboxReport:
        changed = any(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines())
        return SandboxReport(passed=changed, tests_run=42, tests_failed=0 if changed else 42,
                             log_excerpt="42 passed in 3.1s" if changed else "no change to test")


class FixturePatchAuthor:
    DIFF = """--- a/sim_bank/gateways/gateway_b/bank_x_connector.py
+++ b/sim_bank/gateways/gateway_b/bank_x_connector.py
@@ -18,14 +18,24 @@ class BankXConnector:
-    ACK_TIMEOUT_MS = 800
+    ACK_TIMEOUT_MS = 3_000
+    ACK_MAX_ATTEMPTS = 3
 
     def send_credit(self, transfer: Transfer) -> TransferStatus:
         response = self._client.post("/credits", json=transfer.as_payload())
-        ack = self._await_ack(response.request_id, timeout_ms=self.ACK_TIMEOUT_MS)
-        if ack is None:
-            return TransferStatus.PENDING
+        for attempt in range(1, self.ACK_MAX_ATTEMPTS + 1):
+            ack = self._await_ack(response.request_id, timeout_ms=self.ACK_TIMEOUT_MS * attempt)
+            if ack is not None:
+                return TransferStatus.from_ack(ack)
+            status = self._client.get(f"/credits/{response.request_id}/status")
+            if status.is_final:
+                return TransferStatus.from_status_query(status)
+        return TransferStatus.UNCONFIRMED
"""

    def write_patch(self, hypothesis, test_result) -> PatchProposal:
        return PatchProposal(
            repository="simulated-bank",
            target="sim_bank/gateways/gateway_b/bank_x_connector.py",
            summary="Raise the Bank X ack timeout, retry the ack wait with backoff, and fall back to a status query "
                    "so unacknowledged credits resolve to a final state instead of hanging as pending.",
            diff=self.DIFF,
        )


class FixtureContacts:
    def __init__(self, opted_out: set[str]) -> None:
        self._opted_out = opted_out

    def resolve(self, customer_token: str) -> Contact | None:
        digits = "".join(ch for ch in customer_token if ch.isdigit())
        if not digits:
            return None
        return Contact(phone_e164=f"+234803{int(digits):07d}", sms_opt_in=customer_token not in self._opted_out)


class FixtureSmsGateway:
    def __init__(self) -> None:
        self.outbox: dict[str, tuple[str, str]] = {}

    def send(self, to: str, text: str, idempotency_key: str) -> str:
        self.outbox.setdefault(idempotency_key, (to, text))
        return f"sms-{len(self.outbox):05d}"


@dataclass
class DemoWorld:
    ports: Ports
    clock: FixedClock
    events: InMemoryEventPublisher
    bank_data: FixtureBankData
    bank: SimulatedBank
    sms: FixtureSmsGateway
    handoff: ValidatedHandoff
    opted_out: set[str] = field(default_factory=set)


def load_handoff_and_complaints(path: Path = FIXTURE_PATH) -> tuple[ValidatedHandoff, list[Complaint], dict]:
    fixture = IncidentFixture.model_validate_json(path.read_text())
    by_type: dict[ContractObjectType, list] = {}
    for record in fixture.records:
        by_type.setdefault(record.type, []).append(record.validate_payload())
    incident = by_type[ContractObjectType.INCIDENT][0]
    hypotheses = {h.id: h for h in by_type[ContractObjectType.HYPOTHESIS]}
    validations = [t for t in by_type[ContractObjectType.TEST_RESULT] if t.purpose == TestRunPurpose.HYPOTHESIS_VALIDATION]
    winner = max(validations, key=lambda t: t.confidence)
    handoff = ValidatedHandoff(
        incident=incident,
        hypothesis=hypotheses[winner.hypothesis_id],
        test_result=winner,
        rivals=[t for t in validations if t is not winner],
    )
    known_txns = {
        e.payload["transaction_id"]: e
        for e in by_type[ContractObjectType.EVENT]
        if e.code == "ACK_TIMEOUT" and "transaction_id" in e.payload
    }
    return handoff, by_type[ContractObjectType.COMPLAINT], known_txns


def _transfer(txn_id: str, token: str, ts: datetime, amount_kobo: int, rng: random.Random, *, route: str,
              destination: str, error_code: str | None, channel: str | None = None) -> TransactionRecord:
    channel = channel or rng.choice(["mobile", "mobile", "mobile", "ussd", "internet_banking"])
    return TransactionRecord(
        txn_id=txn_id, customer_token=token, ts=ts, amount_kobo=amount_kobo, channel=channel,
        product="bank_transfer", route=route, destination_bank=destination, error_code=error_code,
        app_version=rng.choice(["4.2", "4.1"]) if channel == "mobile" else None,
        debit_status="posted", credit_status="unknown" if error_code else "confirmed",
    )


def _generate(rng: random.Random, complaints: list[Complaint], known_txns: dict, window_start: datetime):
    window_seconds = int((PLAN_TIME - window_start).total_seconds()) - 60
    tokens = [f"cust_demo_{i:04d}" for i in range(1, AFFECTED_CUSTOMERS + 1)]
    complained = {c.customer_token: c for c in complaints if c.customer_token}
    doubles = set(rng.sample(sorted(set(tokens) - set(complained)), CUSTOMERS_WITH_TWO_TXNS))

    def random_amount() -> int:
        return rng.randrange(2, 500) * 50_000

    txns: list[TransactionRecord] = []
    first_txn: dict[str, TransactionRecord] = {}
    retry_seq = 0
    for i, token in enumerate(tokens, start=1):
        txn_id = f"txn_demo_{i:04d}"
        if txn_id in known_txns:
            event = known_txns[txn_id]
            ts, amount, channel = event.ts, naira_to_kobo(event.payload["amount_ngn"]), event.channel
        elif token in complained and complained[token].parsed:
            parsed = complained[token].parsed
            ts, amount, channel = parsed.time or complained[token].ts - timedelta(minutes=1), naira_to_kobo(parsed.amount), complained[token].channel
        else:
            ts, amount, channel = window_start + timedelta(seconds=rng.randrange(window_seconds)), random_amount(), None
        txn = _transfer(txn_id, token, ts, amount, rng, route=BROKEN_ROUTE, destination=DESTINATION,
                        error_code="ACK_TIMEOUT", channel=channel)
        txns.append(txn)
        first_txn[token] = txn
        if token in doubles:
            retry_seq += 1
            retry_ts = min(ts + timedelta(seconds=rng.randrange(20, 240)), window_start + timedelta(seconds=window_seconds))
            txns.append(_transfer(f"txn_demo_r{retry_seq:04d}", token, retry_ts, amount, rng, route=BROKEN_ROUTE,
                                  destination=DESTINATION, error_code="ACK_TIMEOUT"))

    noise = [("gateway_b.bank_y", "BANK_Y"), (HEALTHY_ROUTE, DESTINATION), ("gateway_a.bank_z", "BANK_Z")]
    for i in range(300):
        route, destination = noise[i % 3]
        token = rng.choice(tokens) if i % 15 == 0 else f"cust_noise_{i:04d}"
        txns.append(_transfer(f"txn_noise_{i:04d}", token, window_start + timedelta(seconds=rng.randrange(window_seconds)),
                              random_amount(), rng, route=route, destination=destination, error_code=None))
    for i in range(40):
        txns.append(_transfer(f"txn_early_{i:04d}", f"cust_early_{i:04d}",
                              window_start - timedelta(minutes=rng.randrange(1, 22)), random_amount(), rng,
                              route=BROKEN_ROUTE, destination=DESTINATION, error_code="ACK_TIMEOUT" if i < 5 else None))

    kinds = ["retry", "history_refresh", "balance_refresh", "channel_switch"]
    signals = []
    silent = [t for t in tokens if t not in complained]
    for token in silent:
        roll = rng.random()
        base = first_txn[token].ts
        if roll < 0.38:
            for kind in rng.sample(kinds, rng.randrange(1, 3)):
                signals.append(BehaviourSignal(ts=base + timedelta(seconds=rng.randrange(15, 300)),
                                               customer_token=token, kind=kind, txn_id=first_txn[token].txn_id))
        elif roll < 0.40:
            signals.append(BehaviourSignal(ts=window_start - timedelta(minutes=10), customer_token=token,
                                           kind="balance_refresh"))
    for i in range(50):
        signals.append(BehaviourSignal(ts=window_start + timedelta(minutes=rng.randrange(14)),
                                       customer_token=f"cust_noise_otp_{i:04d}", kind="otp_resend"))

    opted_out = set(rng.sample(silent, 16))
    return txns, signals, opted_out


def build_demo_world(seed: int = SEED, fixture_path: Path = FIXTURE_PATH) -> DemoWorld:
    handoff, complaints, known_txns = load_handoff_and_complaints(fixture_path)
    rng = random.Random(seed)
    txns, signals, opted_out = _generate(rng, complaints, known_txns, incident_window_start(handoff.incident))
    clock = FixedClock(PLAN_TIME)
    events = InMemoryEventPublisher()
    bank_data = FixtureBankData(txns, complaints, signals)
    bank = SimulatedBank()
    sms = FixtureSmsGateway()
    ports = Ports(
        clock=clock, bank=bank_data, twin=bank, routing=bank, ghost_tests=bank, metrics=bank,
        sandbox=FixtureSandbox(), patch_author=FixturePatchAuthor(), contacts=FixtureContacts(opted_out),
        sms=sms, events=events,
    )
    return DemoWorld(ports=ports, clock=clock, events=events, bank_data=bank_data, bank=bank, sms=sms,
                     handoff=handoff, opted_out=opted_out)
