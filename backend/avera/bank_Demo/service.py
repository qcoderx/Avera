"""Single-agent duplicate-debit investigation and verified repair workflow."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from avera.bank_Demo.workspace import RepairWorkspace, SAFE_SOURCE
from avera.observability.model import ModelUnavailable, structured_call
from avera.remediation.approvals.service import ApprovalService
from avera.remediation.models import Approval, ApprovalStatus, CodePatch, SandboxReport


STARTING_BALANCE_NGN = 485_300
TRANSFER_AMOUNT_NGN = 25_000
PublishEvent = Callable[[str, dict[str, object]], Awaitable[None]]
FlushEvents = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(min_length=20, max_length=500)
    replacement_source: str = Field(min_length=80, max_length=4_000)


class BankDemoRuntime:
    """Runs one controlled incident against source code in an isolated workspace."""

    def __init__(
        self,
        language_model=None,
        *,
        workspace: RepairWorkspace | None = None,
        publish_event: PublishEvent | None = None,
        approval_service: ApprovalService | None = None,
        flush_approval_events: FlushEvents | None = None,
    ) -> None:
        self.language_model = language_model
        self.workspace = workspace or RepairWorkspace()
        self.publish_event = publish_event
        self.approval_service = approval_service
        self.flush_approval_events = flush_approval_events
        self.step_delay = max(0.0, float(os.getenv("AVERA_BANK_DEMO_STEP_DELAY", "0.1")))
        self.model_timeout = max(1.0, float(os.getenv("AVERA_BANK_DEMO_MODEL_TIMEOUT", "4")))
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._approval_id: str | None = None
        self._approval_status: ApprovalStatus | None = None
        self._approval_event: asyncio.Event | None = None
        self._state = self._idle_state()
        if self.approval_service is not None:
            self.approval_service.subscribe(self._on_approval_decision)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _idle_state(self) -> dict[str, object]:
        return {
            "status": "idle",
            "account": {
                "name": "Amara Okafor",
                "account_number": "•••• 2048",
                "balance_ngn": STARTING_BALANCE_NGN,
                "available_ngn": STARTING_BALANCE_NGN,
            },
            "beneficiary": {
                "name": "Tobi Adeyemi",
                "bank": "Bank X",
                "account_number": "0123456789",
            },
            "transfer": {
                "amount_ngn": TRANSFER_AMOUNT_NGN,
                "intent_id": None,
                "attempts": 0,
                "debits": 0,
            },
            "ledger": self._background_activity(),
            "incident": None,
            "steps": self._steps(),
            "report": None,
            "workspace": {"source_file": self.workspace.display_source_path},
            "updated_at": self._now(),
        }

    @staticmethod
    def _background_activity() -> list[dict[str, object]]:
        return [
            {
                "id": "TXN-7712",
                "title": "Salary credit",
                "subtitle": "Avera Studios · Yesterday",
                "amount_ngn": 320_000,
                "direction": "credit",
                "status": "completed",
            },
            {
                "id": "TXN-7708",
                "title": "Data subscription",
                "subtitle": "Airtime & data · 18 Oct",
                "amount_ngn": 4_500,
                "direction": "debit",
                "status": "completed",
            },
        ]

    @staticmethod
    def _steps() -> list[dict[str, object]]:
        return [
            {"id": "report", "label": "Customer report received", "status": "pending", "detail": None, "at": None},
            {"id": "investigate", "label": "Issue reproduced", "status": "pending", "detail": None, "at": None},
            {"id": "diagnose", "label": "Root cause identified", "status": "pending", "detail": None, "at": None},
            {"id": "patch", "label": "Source code patched", "status": "pending", "detail": None, "at": None},
            {"id": "test", "label": "Regression tests passed", "status": "pending", "detail": None, "at": None},
            {"id": "verify", "label": "Original journey verified", "status": "pending", "detail": None, "at": None},
        ]

    def _simulate_transfer(
        self,
        intent_id: str,
        *,
        amount_ngn: int = TRANSFER_AMOUNT_NGN,
        opening_balance_ngn: int = STARTING_BALANCE_NGN,
        existing_ledger: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        ledger: list[dict[str, object]] = []
        balance = opening_balance_ngn
        route = self.workspace.gateway_route("BANK_X", gateway_a_healthy=False)
        attempts = 2 if route == "gateway_a.bank_x" else 1
        for attempt in range(1, attempts + 1):
            balance -= amount_ngn
            ledger.append({
                "id": f"TXN-{intent_id[-4:]}-{attempt}",
                "title": "Transfer to Tobi Adeyemi",
                "subtitle": "Bank X · Today, 10:42",
                "amount_ngn": amount_ngn,
                "direction": "debit",
                "status": "completed",
                "gateway_route": route,
            })
        return {
            "balance_ngn": balance,
            "debits": len(ledger),
            "attempts": attempts,
            "ledger": ledger + (existing_ledger or self._background_activity()),
            "route": route,
        }

    async def state(self) -> dict[str, object]:
        async with self._lock:
            return copy.deepcopy(self._state)

    async def _emit(self, object_type: str, payload: dict[str, object]) -> None:
        if self.publish_event is None:
            return
        try:
            await self.publish_event(object_type, payload)
        except Exception:
            logger.exception("Bank demo record could not be published to the Avera stream")

    def _bank_event(
        self,
        event_id: str,
        code: str,
        *,
        payload: dict[str, object] | None = None,
        service: str = "gateway_a",
        route: str = "gateway_a.bank_x",
        customer_token: str = "cust-demo-amara",
    ) -> dict[str, object]:
        return {
            "id": event_id,
            "ts": self._now(),
            "source": "mock-bank",
            "channel": "mobile",
            "service": service,
            "route": route,
            "code": code,
            "latency_ms": 1_200 if code == "ACK_TIMEOUT" else 42,
            "customer_token": customer_token,
            "payload": payload or {},
        }

    def _incident_payload(self, status: str) -> dict[str, object]:
        incident = self._state["incident"]
        intent_id = str(self._state["transfer"]["intent_id"])
        suffix = intent_id.rsplit("-", 1)[-1]
        return {
            "id": incident["id"],
            "status": status,
            "opened_at": incident["created_at"],
            "signals": [
                f"bank-{suffix}-transfer",
                f"bank-{suffix}-timeout",
                f"bank-{suffix}-duplicate",
                f"complaint-{suffix}",
                f"complaint-{suffix}-02",
                f"complaint-{suffix}-03",
                f"complaint-{suffix}-04",
            ],
            "fingerprint": {
                "groups": [{
                    "gateway": "gateway_a",
                    "destination": "BANK_X",
                    "failures": 4,
                    "successes": 0,
                }],
                "behaviours": [{"kind": "route_stuck_after_health_failure", "count": 4}],
                "symptom": "duplicate debits while Gateway A is unavailable",
            },
            "suspect_zones": ["gateway_a", "payments_integration"],
        }

    async def _emit_resolution(
        self,
        action: str,
        status: str,
        *,
        detail: dict[str, object],
        verified: bool = False,
    ) -> None:
        incident_id = str(self._state["incident"]["id"])
        await self._emit("resolution_event", {
            "id": f"resolution-{incident_id.lower()}-{action}",
            "incident_id": incident_id,
            "ts": self._now(),
            "action": action,
            "status": status,
            "verified_by_ghost_test": verified,
            "detail": detail,
        })

    def _on_approval_decision(self, approval: Approval) -> None:
        if approval.id != self._approval_id:
            return
        self._approval_status = approval.status
        if self._approval_event is not None:
            self._approval_event.set()

    async def _request_patch_approval(
        self,
        *,
        proposal: RepairProposal,
        replacement_source: str,
        before: str,
        hypothesis_id: str,
        suffix: str,
    ) -> None:
        if self.approval_service is None:
            return

        proposed_diff = self.workspace.diff(before, replacement_source)
        self.workspace.write_source(replacement_source)
        sandbox = await asyncio.to_thread(self.workspace.run_tests)
        self.workspace.reset()
        if not sandbox.passed:
            raise RuntimeError("the proposed patch failed its isolated regression tests")

        approval = self.approval_service.request(
            payload=CodePatch(
                incident_id=str(self._state["incident"]["id"]),
                plan_id=f"plan-bank-{suffix}",
                hypothesis_id=hypothesis_id,
                repository="Avera mock bank",
                target=self.workspace.display_source_path,
                summary="Fail Bank X traffic over to Gateway B while Gateway A is unhealthy.",
                diff=proposed_diff,
                sandbox=SandboxReport(
                    passed=True,
                    tests_run=3,
                    tests_failed=0,
                    log_excerpt=sandbox.output[-500:],
                ),
            ),
            subject_key=f"bank-demo:{suffix}:code-patch",
            requested_by="resolution_agent",
        )
        self._approval_id = approval.id
        self._approval_status = None
        self._approval_event = asyncio.Event()
        self._state["status"] = "awaiting_approval"
        self._set_step(
            "patch",
            "active",
            "The proposed source diff is awaiting engineer approval in Avera.",
        )
        if self.flush_approval_events is not None:
            await self.flush_approval_events()
        await self._emit_resolution(
            "review_bank_transfer_patch",
            "awaiting_approval",
            detail={"approval_id": approval.id, "file": self.workspace.display_source_path},
        )
        await self._approval_event.wait()
        if self._approval_status == ApprovalStatus.REJECTED:
            raise RuntimeError("the proposed transfer patch was rejected by the operator")
        if self._approval_status != ApprovalStatus.APPROVED:
            raise RuntimeError("the proposed transfer patch did not receive approval")

    async def transfer(self, amount_ngn: int = TRANSFER_AMOUNT_NGN) -> dict[str, object]:
        async with self._lock:
            if self._task is not None and not self._task.done():
                raise RuntimeError("the reported transfer issue is still being reviewed")
            if self._state["status"] not in {"idle", "resolved", "failed"}:
                raise RuntimeError("report the timed-out transfer before trying again")

            repaired = self._state["status"] == "resolved"
            opening_balance = int(self._state["account"]["available_ngn"])
            existing_ledger = list(self._state["ledger"])
            intent_id = f"intent-bank-x-{uuid4().hex[:8]}"
            reproduced = self._simulate_transfer(
                intent_id,
                amount_ngn=amount_ngn,
                opening_balance_ngn=opening_balance,
                existing_ledger=existing_ledger,
            )
            if not repaired:
                self._state = self._idle_state()
            self._state.update({
                "status": "resolved" if repaired else "awaiting_report",
                "account": {
                    **self._state["account"],
                    "balance_ngn": reproduced["balance_ngn"],
                    "available_ngn": reproduced["balance_ngn"],
                },
                "transfer": {
                    "amount_ngn": amount_ngn,
                    "intent_id": intent_id,
                    "attempts": reproduced["attempts"],
                    "debits": reproduced["debits"],
                    "route": reproduced["route"],
                },
                "ledger": reproduced["ledger"],
                "updated_at": self._now(),
            })
            suffix = intent_id.rsplit("-", 1)[-1]
            common = {
                "transfer_intent_id": intent_id,
                "amount_ngn": amount_ngn,
                "destination_bank": "BANK_X",
            }
            await self._emit("event", self._bank_event(
                f"bank-{suffix}-transfer",
                "TRANSFER_REQUESTED",
                payload={**common, "demo_run_start": not repaired},
                route=str(reproduced["route"]),
            ))
            if repaired:
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-ack",
                    "ACK_RECEIVED",
                    payload=common,
                    service="gateway_b",
                    route="gateway_b.bank_x",
                ))
            else:
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-timeout",
                    "ACK_TIMEOUT",
                    payload=common,
                    service="gateway_a",
                ))
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-duplicate",
                    "DUPLICATE_DEBIT_POSTED",
                    payload={
                        **common,
                        "debit_count": reproduced["debits"],
                        "failed_gateway": "gateway_a",
                    },
                    service="core-banking",
                ))
            return copy.deepcopy(self._state)

    async def report_issue(self, complaint: str) -> dict[str, object]:
        async with self._lock:
            if self._state["status"] != "awaiting_report":
                raise RuntimeError("there is no unreported transfer issue")
            self._state.update({
                "status": "reported",
                "incident": {
                    "id": "INC-BANK-001",
                    "area": "Outbound transfers",
                    "title": "Gateway A outage duplicated Bank X debits",
                    "complaint": complaint,
                    "reported_by": "Amara Okafor",
                    "complaint_count": 4,
                    "severity": "High",
                    "created_at": self._now(),
                },
                "updated_at": self._now(),
            })
            self._complete_step(
                "report",
                "Four customers reported duplicate debits on timed-out Bank X transfers.",
            )
            intent_id = str(self._state["transfer"]["intent_id"])
            suffix = intent_id.rsplit("-", 1)[-1]
            complaints = [
                (f"complaint-{suffix}", "cust-demo-amara", complaint, 25_000, intent_id, "in_app"),
                (
                    f"complaint-{suffix}-02",
                    "cust-demo-chidi",
                    "I sent money to my friend. It failed, but my account was charged twice.",
                    18_500,
                    f"intent-bank-x-{suffix}-02",
                    "support_ticket",
                ),
                (
                    f"complaint-{suffix}-03",
                    "cust-demo-zainab",
                    "The transfer timed out and I can see two debits for the same payment.",
                    40_000,
                    f"intent-bank-x-{suffix}-03",
                    "whatsapp",
                ),
                (
                    f"complaint-{suffix}-04",
                    "cust-demo-kunle",
                    "I sent money to my friend once and was debited two times.",
                    12_000,
                    f"intent-bank-x-{suffix}-04",
                    "in_app",
                ),
            ]
            for complaint_id, customer, text, amount, matched_txn, channel in complaints:
                await self._emit("complaint", {
                    "id": complaint_id,
                    "ts": self._now(),
                    "channel": channel,
                    "raw_text": text,
                    "customer_token": customer,
                    "parsed": {
                        "type": "duplicate_debit",
                        "amount": amount,
                        "product": "outbound_transfer",
                        "matched_txn": matched_txn,
                    },
                })
            await self._emit("incident", self._incident_payload("emerging"))
            self._task = asyncio.create_task(self._repair(), name="avera-bank-demo-repair")
            return copy.deepcopy(self._state)

    async def trigger(self) -> dict[str, object]:
        """Backwards-compatible one-call trigger used by the command-centre demo."""
        await self.reset()
        await self.transfer()
        return await self.report_issue(
            "I sent money to my friend, it did not go through, and I was debited twice."
        )

    async def start_repair(self) -> dict[str, object]:
        async with self._lock:
            if self._state["status"] in {"idle", "awaiting_report"}:
                raise RuntimeError("report the duplicate-debit incident first")
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._repair(), name="avera-bank-demo-repair")
            return copy.deepcopy(self._state)

    async def wait_for_repair(self) -> dict[str, object]:
        task = self._task
        if task is not None:
            await task
        return await self.state()

    async def reset(self) -> dict[str, object]:
        async with self._lock:
            if self._task is not None and not self._task.done():
                self._task.cancel()
            self._task = None
            if self.approval_service is not None and self._approval_id is not None:
                try:
                    approval = self.approval_service.get(self._approval_id)
                    if approval.status == ApprovalStatus.PENDING:
                        self.approval_service.withdraw(
                            incident_id=approval.incident_id,
                            subject_key=approval.subject_key,
                            requested_by="system",
                        )
                        if self.flush_approval_events is not None:
                            await self.flush_approval_events()
                except Exception:
                    logger.exception("Bank demo approval could not be retired during reset")
            self._approval_id = None
            self._approval_status = None
            self._approval_event = None
            self.workspace.reset()
            self._state = self._idle_state()
            return copy.deepcopy(self._state)

    def _set_step(self, step_id: str, status: Literal["active", "complete", "failed"], detail: str | None = None) -> None:
        step = next(item for item in self._state["steps"] if item["id"] == step_id)
        step.update({"status": status, "detail": detail, "at": self._now()})
        self._state["updated_at"] = self._now()

    def _complete_step(self, step_id: str, detail: str) -> None:
        self._set_step(step_id, "complete", detail)

    async def _pause(self) -> None:
        if self.step_delay:
            await asyncio.sleep(self.step_delay)

    async def _proposal(self, source: str) -> tuple[RepairProposal, str]:
        instructions = (
            "You are repairing one isolated Python source file for a simulated bank. "
            "Gateway A is unhealthy, but Bank X traffic remains pinned to it. The timeout "
            "path replays transfers and produces duplicate debits for several customers. "
            "Return the full replacement module without Markdown fences. Keep exactly one "
            "function named select_gateway(destination_bank, gateway_a_healthy), preserve "
            "the two ValueError guards, use no imports or external calls, keep healthy "
            "Gateway A traffic unchanged, and route Bank X to gateway_b.bank_x when "
            "Gateway A is unhealthy."
        )
        context = {
            "complaint": self._state["incident"]["complaint"],
            "observed_debits": self._state["transfer"]["debits"],
            "source_file": self.workspace.display_source_path,
            "current_source": source,
            "required_regressions": [
                "unhealthy Gateway A routes Bank X to Gateway B",
                "healthy Gateway A remains the Bank X route",
                "empty and unsupported destinations raise ValueError",
            ],
        }
        try:
            proposal = await asyncio.wait_for(
                structured_call(
                    self.language_model,
                    instructions,
                    context,
                    RepairProposal,
                    validate=lambda value: self._validate_proposal(value),
                ),
                timeout=self.model_timeout,
            )
            return proposal, "gpt"
        except (ModelUnavailable, TimeoutError):
            return RepairProposal(
                root_cause=(
                    "Bank X remained pinned to Gateway A after its health check failed. The "
                    "timeout path replayed each transfer against the unavailable route after "
                    "the ledger debit, producing a second debit for affected customers."
                ),
                replacement_source=SAFE_SOURCE,
            ), "verified fallback"

    def _validate_proposal(self, proposal: RepairProposal) -> RepairProposal:
        self.workspace.validate_source(proposal.replacement_source)
        return proposal

    async def _repair(self) -> None:
        before = self.workspace.read_source()
        try:
            self._state["status"] = "investigating"
            self._set_step("investigate", "active", "Replaying the timed-out Bank X transfer.")
            await self._emit("incident", self._incident_payload("investigating"))
            await self._pause()
            reproduced = self._simulate_transfer(self._state["transfer"]["intent_id"])
            if reproduced["debits"] != 2:
                raise RuntimeError("the duplicate debit could not be reproduced")
            self._complete_step(
                "investigate",
                "Four complaints share the failed Gateway A → Bank X route; replay produced two debits.",
            )
            intent_id = str(self._state["transfer"]["intent_id"])
            suffix = intent_id.rsplit("-", 1)[-1]
            hypothesis_id = f"hyp-bank-{suffix}"
            await self._emit("hypothesis", {
                "id": hypothesis_id,
                "claim": "Bank X is still routed through failed Gateway A, so timeout replay posts a second debit.",
                "zone": "gateway_a",
                "evidence_for": [f"bank-{suffix}-timeout", f"bank-{suffix}-duplicate"],
                "evidence_against": [],
                "disproof": "With Gateway A unhealthy, Bank X must route through Gateway B and produce one debit.",
                "routed_to": ["ghost_test_agent"],
                "prior_rank": 1,
            })
            await self._emit_resolution(
                "reproduce_bank_transfer_failure",
                "reproduced",
                detail={"transfer_intent_id": intent_id, "attempts": 2, "debits": 2},
            )

            self._set_step("diagnose", "active", "Reading the Bank X gateway routing source.")
            proposal, repair_source = await self._proposal(before)
            await self._pause()
            self._complete_step("diagnose", proposal.root_cause)
            await self._emit("test_result", {
                "id": f"test-bank-{suffix}",
                "hypothesis_id": hypothesis_id,
                "purpose": "hypothesis_validation",
                "test_plan": "Replay affected Bank X transfers with Gateway A marked unhealthy.",
                "runs": 3,
                "reproduced": 3,
                "trace": [
                    {"service": "gateway_a", "result": "health_check_failed", "latency_ms": 1_200, "error_code": "GATEWAY_UNAVAILABLE"},
                    {"service": "payment-router", "result": "bank_x_route_remained_gateway_a", "latency_ms": 41},
                    {"service": "core-banking", "result": "timeout_replay_debited_twice", "latency_ms": 38},
                ],
                "confidence": 0.99,
                "verdict": "validated",
                "disproof_passed": True,
            })
            await self._emit("incident", self._incident_payload("validated"))
            await self._emit("blast_radius", {
                "id": f"blast-bank-{suffix}",
                "txn_count": 8,
                "customer_count": 4,
                "silent_count": 0,
                "exposure_ngn": 95_500,
                "customers": [
                    "cust-demo-amara",
                    "cust-demo-chidi",
                    "cust-demo-zainab",
                    "cust-demo-kunle",
                ],
                "customers_complete": True,
            })
            await self._emit("fix_plan", {
                "id": f"plan-bank-{suffix}",
                "steps": [
                    {
                        "title": "Fail Bank X over to Gateway B",
                        "description": "Use the healthy route whenever Gateway A's health check is down.",
                        "owner": "resolution_agent",
                    },
                    {
                        "title": "Run gateway routing regression tests",
                        "description": "Prove failed Gateway A traffic moves to Gateway B and healthy routing stays stable.",
                        "owner": "ghost_test_agent",
                    },
                ],
                "owners": ["resolution_agent", "ghost_test_agent"],
                "rollback": "Restore the isolated source snapshot if any regression fails.",
                "verification": "Replay a Bank X transfer during the Gateway A outage and observe Gateway B with one debit.",
                "eta_containment": "under 1 minute",
                "eta_full": "under 2 minutes",
            })

            await self._request_patch_approval(
                proposal=proposal,
                replacement_source=proposal.replacement_source,
                before=before,
                hypothesis_id=hypothesis_id,
                suffix=suffix,
            )

            self._state["status"] = "patching"
            await self._emit("incident", self._incident_payload("resolving"))
            self._set_step("patch", "active", f"Editing {self.workspace.display_source_path}.")
            self.workspace.write_source(proposal.replacement_source)
            after = self.workspace.read_source()
            await self._pause()
            self._complete_step(
                "patch",
                "Bank X now fails over to Gateway B whenever Gateway A is unhealthy.",
            )
            await self._emit_resolution(
                "apply_bank_transfer_patch",
                "applied",
                detail={"file": self.workspace.display_source_path, "repair_source": repair_source},
            )

            self._set_step("test", "active", "Running the isolated gateway routing regression suite.")
            test_run = await asyncio.to_thread(self.workspace.run_tests)
            if not test_run.passed:
                raise RuntimeError(f"regression suite failed: {test_run.output[-500:]}")
            await self._pause()
            self._complete_step("test", "3 regression tests passed with the patched source.")
            await self._emit_resolution(
                "run_bank_regression_tests",
                "verified_pass",
                detail={"tests_passed": 3, "command": test_run.command},
                verified=True,
            )

            self._set_step("verify", "active", "Repeating the Bank X journey with Gateway A unavailable.")
            verified = self._simulate_transfer(self._state["transfer"]["intent_id"])
            if verified["debits"] != 1 or verified["route"] != "gateway_b.bank_x":
                raise RuntimeError("verification did not fail Bank X over to Gateway B with one debit")
            await self._pause()
            self._complete_step(
                "verify",
                "Gateway A stayed isolated; Bank X used Gateway B and produced one debit.",
            )
            await self._emit_resolution(
                "verify_bank_transfer_journey",
                "verified_pass",
                detail={
                    "transfer_intents": 1,
                    "failed_route": "gateway_a.bank_x",
                    "verified_route": "gateway_b.bank_x",
                    "debits": 1,
                },
                verified=True,
            )

            reversal_amount = int(self._state["transfer"]["amount_ngn"])
            reversal_id = f"REV-{suffix.upper()}"
            restored_balance = int(self._state["account"]["available_ngn"]) + reversal_amount
            self._state.update({
                "account": {
                    **self._state["account"],
                    "balance_ngn": restored_balance,
                    "available_ngn": restored_balance,
                },
                "ledger": [{
                    "id": reversal_id,
                    "title": "Duplicate debit reversal",
                    "subtitle": "Transfer refund · Today",
                    "amount_ngn": reversal_amount,
                    "direction": "credit",
                    "status": "completed",
                    "related_transfer_intent_id": intent_id,
                }, *self._state["ledger"]],
            })
            await self._emit("event", self._bank_event(
                f"bank-{suffix}-reversal",
                "DUPLICATE_DEBIT_REVERSED",
                payload={
                    "transfer_intent_id": intent_id,
                    "amount_ngn": reversal_amount,
                    "reversal_id": reversal_id,
                    "destination_bank": "BANK_X",
                },
                service="core-banking",
            ))
            await self._emit_resolution(
                "reverse_duplicate_debit",
                "applied",
                detail={"amount_ngn": reversal_amount, "reversal_id": reversal_id},
                verified=True,
            )

            self._state.update({
                "status": "resolved",
                "report": {
                    "status": "resolved",
                    "root_cause": proposal.root_cause,
                    "files_changed": [self.workspace.display_source_path],
                    "diff": self.workspace.diff(before, after),
                    "test_command": test_run.command,
                    "tests_passed": 3,
                    "test_output": test_run.output,
                    "verification": "Gateway A failed · Bank X rerouted to Gateway B · 1 debit",
                    "repair_source": repair_source,
                },
                "updated_at": self._now(),
            })
            await self._emit("incident", self._incident_payload("resolved"))
            await self._emit_resolution(
                "mark_incident_resolved",
                "resolved",
                detail={"root_cause": proposal.root_cause, "tests_passed": 3},
                verified=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state["status"] = "failed"
            active = next((item for item in self._state["steps"] if item["status"] == "active"), None)
            if active is not None:
                self._set_step(active["id"], "failed", str(exc))
            self._state["report"] = {
                "status": "failed",
                "error": str(exc),
                "files_changed": [],
            }
            self._state["updated_at"] = self._now()
            if self._state.get("incident") is not None:
                await self._emit_resolution(
                    "bank_transfer_repair_failed",
                    "failed",
                    detail={"error": str(exc)},
                )

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.workspace.close()
