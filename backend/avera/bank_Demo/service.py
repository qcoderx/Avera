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
        self.step_delay = max(0.0, float(os.getenv("AVERA_BANK_DEMO_STEP_DELAY", "0.7")))
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
        seen: set[str] = set()
        ledger: list[dict[str, object]] = []
        balance = opening_balance_ngn
        for attempt in (1, 2):
            key = self.workspace.idempotency_key(intent_id, attempt)
            if key in seen:
                continue
            seen.add(key)
            balance -= amount_ngn
            ledger.append({
                "id": f"TXN-{intent_id[-4:]}-{attempt}",
                "title": "Transfer to Tobi Adeyemi",
                "subtitle": "Bank X · Today, 10:42",
                "amount_ngn": amount_ngn,
                "direction": "debit",
                "status": "completed",
                "idempotency_key": key,
            })
        return {
            "balance_ngn": balance,
            "debits": len(ledger),
            "ledger": ledger + (existing_ledger or self._background_activity()),
            "keys": sorted(seen),
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
        service: str = "transfer-service",
    ) -> dict[str, object]:
        return {
            "id": event_id,
            "ts": self._now(),
            "source": "mock-bank",
            "channel": "mobile",
            "service": service,
            "route": "transfer-service.bank_x",
            "code": code,
            "latency_ms": 1_200 if code == "ACK_TIMEOUT" else 42,
            "customer_token": "cust-demo-amara",
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
                f"bank-{suffix}-retry",
                f"bank-{suffix}-duplicate",
                f"complaint-{suffix}",
            ],
            "fingerprint": {
                "groups": [{
                    "gateway": "transfer-service",
                    "destination": "BANK_X",
                    "failures": 2,
                    "successes": 0,
                }],
                "behaviours": [{"kind": "retries", "count": 1}],
                "symptom": "duplicate debit after acknowledgement timeout",
            },
            "suspect_zones": ["transfer_idempotency", "provider_retry"],
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
                summary="Make timeout retries reuse the original transfer idempotency key.",
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
                    "attempts": 2,
                    "debits": reproduced["debits"],
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
            ))
            if repaired:
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-ack",
                    "ACK_RECEIVED",
                    payload=common,
                    service="payment-router",
                ))
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-credit",
                    "CREDIT_ACCEPTED",
                    payload=common,
                    service="destination-bank-bank_x",
                ))
            else:
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-timeout",
                    "ACK_TIMEOUT",
                    payload=common,
                    service="payment-router",
                ))
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-retry",
                    "RETRY_LOOP",
                    payload={**common, "attempt": 2},
                    service="transfer-ui",
                ))
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-credit",
                    "CREDIT_ACCEPTED",
                    payload=common,
                    service="destination-bank-bank_x",
                ))
                await self._emit("event", self._bank_event(
                    f"bank-{suffix}-duplicate",
                    "DUPLICATE_DEBIT_POSTED",
                    payload={**common, "debit_count": reproduced["debits"]},
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
                    "title": "One transfer created two account debits",
                    "complaint": complaint,
                    "reported_by": "Amara Okafor",
                    "severity": "High",
                    "created_at": self._now(),
                },
                "updated_at": self._now(),
            })
            self._complete_step(
                "report",
                "Customer reported two debits after one timed-out transfer.",
            )
            intent_id = str(self._state["transfer"]["intent_id"])
            suffix = intent_id.rsplit("-", 1)[-1]
            await self._emit("complaint", {
                "id": f"complaint-{suffix}",
                "ts": self._now(),
                "channel": "in_app",
                "raw_text": complaint,
                "customer_token": "cust-demo-amara",
                "parsed": {
                    "type": "duplicate_debit",
                    "amount": self._state["transfer"]["amount_ngn"],
                    "product": "outbound_transfer",
                    "matched_txn": intent_id,
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
            "I made one ₦25,000 transfer, but my account was debited twice after it timed out."
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
            "The customer initiated one transfer. A provider acknowledgement timed out, "
            "then attempt 2 created a second debit because the idempotency key changed. "
            "Return the full replacement module without Markdown fences. Keep exactly one "
            "function named build_idempotency_key(transfer_intent_id, attempt), preserve "
            "the two ValueError guards, use no imports or external calls, and make retries "
            "share the original transfer-intent key while different intents remain unique."
        )
        context = {
            "complaint": self._state["incident"]["complaint"],
            "observed_debits": self._state["transfer"]["debits"],
            "source_file": self.workspace.display_source_path,
            "current_source": source,
            "required_regressions": [
                "same intent attempts 1 and 2 return the same key",
                "different intents return different keys",
                "invalid intent and attempt values raise ValueError",
            ],
        }
        try:
            proposal = await structured_call(
                self.language_model,
                instructions,
                context,
                RepairProposal,
                validate=lambda value: self._validate_proposal(value),
            )
            return proposal, "gpt"
        except ModelUnavailable:
            return RepairProposal(
                root_cause=(
                    "The retry path included the attempt number in the provider idempotency key, "
                    "so the second delivery was processed as a new transfer intent."
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
                "One transfer intent produced two provider keys and two ledger debits.",
            )
            intent_id = str(self._state["transfer"]["intent_id"])
            suffix = intent_id.rsplit("-", 1)[-1]
            hypothesis_id = f"hyp-bank-{suffix}"
            await self._emit("hypothesis", {
                "id": hypothesis_id,
                "claim": "The transfer retry changes its idempotency key and posts a second debit.",
                "zone": "transfer_idempotency",
                "evidence_for": [f"bank-{suffix}-timeout", f"bank-{suffix}-duplicate"],
                "evidence_against": [],
                "disproof": "Two attempts for one transfer intent must produce one provider key and one debit.",
                "routed_to": ["ghost_test_agent"],
                "prior_rank": 1,
            })
            await self._emit_resolution(
                "reproduce_bank_transfer_failure",
                "reproduced",
                detail={"transfer_intent_id": intent_id, "attempts": 2, "debits": 2},
            )

            self._set_step("diagnose", "active", "Reading the retry and idempotency source.")
            proposal, repair_source = await self._proposal(before)
            await self._pause()
            self._complete_step("diagnose", proposal.root_cause)
            await self._emit("test_result", {
                "id": f"test-bank-{suffix}",
                "hypothesis_id": hypothesis_id,
                "purpose": "hypothesis_validation",
                "test_plan": "Replay one transfer intent through the acknowledgement timeout and retry path.",
                "runs": 3,
                "reproduced": 3,
                "trace": [
                    {"service": "transfer-service", "result": "attempt_1_debited", "latency_ms": 38},
                    {"service": "payment-router", "result": "ack_timeout", "latency_ms": 1_200, "error_code": "ACK_TIMEOUT"},
                    {"service": "transfer-service", "result": "attempt_2_debited", "latency_ms": 41},
                ],
                "confidence": 0.99,
                "verdict": "validated",
                "disproof_passed": True,
            })
            await self._emit("incident", self._incident_payload("validated"))
            await self._emit("blast_radius", {
                "id": f"blast-bank-{suffix}",
                "txn_count": 2,
                "customer_count": 1,
                "silent_count": 0,
                "exposure_ngn": self._state["transfer"]["amount_ngn"],
                "customers": ["cust-demo-amara"],
                "customers_complete": True,
            })
            await self._emit("fix_plan", {
                "id": f"plan-bank-{suffix}",
                "steps": [
                    {
                        "title": "Patch transfer idempotency",
                        "description": "Keep one provider key for every retry of the original transfer intent.",
                        "owner": "resolution_agent",
                    },
                    {
                        "title": "Run transfer regression tests",
                        "description": "Prove retries cannot create a second debit and different intents stay unique.",
                        "owner": "ghost_test_agent",
                    },
                ],
                "owners": ["resolution_agent", "ghost_test_agent"],
                "rollback": "Restore the isolated source snapshot if any regression fails.",
                "verification": "Repeat the timed-out transfer journey and observe one debit.",
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
                "The retry now keeps one key for the original customer transfer intent.",
            )
            await self._emit_resolution(
                "apply_bank_transfer_patch",
                "applied",
                detail={"file": self.workspace.display_source_path, "repair_source": repair_source},
            )

            self._set_step("test", "active", "Running the isolated transfer regression suite.")
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

            self._set_step("verify", "active", "Repeating the original timeout and retry journey.")
            verified = self._simulate_transfer(self._state["transfer"]["intent_id"])
            if verified["debits"] != 1:
                raise RuntimeError("verification still produced more than one debit")
            await self._pause()
            self._complete_step(
                "verify",
                "Two delivery attempts produced one debit for the original transfer intent.",
            )
            await self._emit_resolution(
                "verify_bank_transfer_journey",
                "verified_pass",
                detail={"transfer_intents": 1, "delivery_attempts": 2, "debits": 1},
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
                    "verification": "1 transfer intent · 2 delivery attempts · 1 debit",
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
