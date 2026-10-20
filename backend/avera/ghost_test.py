"""Deterministic Ghost Test Agent for safe hypothesis checks and post-fix reruns."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field, field_validator

from avera.contracts import (
    ContractModel,
    ControlResult,
    Hypothesis,
    TestResult,
    TestRunPurpose,
    TestVerdict,
    TraceStep,
)
from avera.events.service import EventStreamService
from avera.simulation.bank import SimulatedBank, TransferResult, TransferStatus

HANDOFF_THRESHOLD = 0.85
POSTING_SLO_MS = 250
CONFIDENCE_CALIBRATION = 0.96


class HypothesisSubmission(ContractModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=20)
    runs: int = Field(default=5, ge=1, le=20)

    @field_validator("hypotheses")
    @classmethod
    def hypothesis_ids_must_be_unique(cls, hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        ids = [item.id for item in hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis ids must be unique within a submitted set")
        return hypotheses


class HypothesisEvaluation(ContractModel):
    hypothesis: Hypothesis
    test_result: TestResult


class HypothesisEvaluationSet(ContractModel):
    evaluations: list[HypothesisEvaluation]
    handoff_threshold: float = HANDOFF_THRESHOLD
    validated_hypothesis_ids: list[str]
    send_back_to_observability: bool


class RerunRequest(ContractModel):
    hypothesis_id: str = Field(min_length=1)
    runs: int = Field(default=5, ge=1, le=20)


class RerunReceipt(ContractModel):
    test_result: TestResult
    route_used: str


class UnknownHypothesisError(LookupError):
    """Raised when a rerun is requested before its original hypothesis was tested."""


class RerunNotReadyError(ValueError):
    """Raised when the original hypothesis did not clear the validation threshold."""


class GhostTestAgent:
    """Run controlled synthetic checks without mutating the live simulated bank.

    The scoring adapter is deliberately deterministic and local. Replacing it
    with a richer twin later does not change the hypothesis or TestResult API.
    """

    def __init__(self, bank: SimulatedBank, event_stream: EventStreamService) -> None:
        self.bank = bank
        self.event_stream = event_stream
        self._lock = asyncio.Lock()
        self._run_number = 0
        self._record_number = 0
        self._hypotheses: dict[str, Hypothesis] = {}
        self._original_results: dict[str, TestResult] = {}
        self._release_seen = False

    async def reset(self) -> None:
        """Discard demo-specific hypothesis state while preserving unique run IDs."""
        async with self._lock:
            self._hypotheses.clear()
            self._original_results.clear()
            self._release_seen = False

    async def evaluate(self, submission: HypothesisSubmission) -> HypothesisEvaluationSet:
        async with self._lock:
            self._run_number += 1
            run_label = f"{self._run_number:06d}"
            evaluations: list[HypothesisEvaluation] = []
            for index, hypothesis in enumerate(submission.hypotheses):
                test_result = self._run_hypothesis(
                    hypothesis,
                    runs=submission.runs,
                    run_id=f"ghost-eval-{run_label}-{index + 1:02d}",
                )
                self._hypotheses[hypothesis.id] = hypothesis
                self._original_results[hypothesis.id] = test_result
                await self._append_result_pair(hypothesis, test_result)
                evaluations.append(HypothesisEvaluation(hypothesis=hypothesis, test_result=test_result))

            validated = [
                item.hypothesis.id
                for item in evaluations
                if item.test_result.confidence >= HANDOFF_THRESHOLD
                and item.test_result.verdict == TestVerdict.VALIDATED
            ]
            return HypothesisEvaluationSet(
                evaluations=evaluations,
                validated_hypothesis_ids=validated,
                send_back_to_observability=not bool(validated),
            )

    async def rerun(self, request: RerunRequest) -> RerunReceipt:
        async with self._lock:
            hypothesis = self._hypotheses.get(request.hypothesis_id)
            original = self._original_results.get(request.hypothesis_id)
            if hypothesis is None or original is None:
                raise UnknownHypothesisError(request.hypothesis_id)
            if (
                original.purpose != TestRunPurpose.HYPOTHESIS_VALIDATION
                or original.verdict != TestVerdict.VALIDATED
                or original.confidence < HANDOFF_THRESHOLD
            ):
                raise RerunNotReadyError(
                    f"Hypothesis {request.hypothesis_id!r} must first clear the {HANDOFF_THRESHOLD:.0%} handoff threshold"
                )

            self._run_number += 1
            route = self.bank.payment_router.route_for("BANK_X")
            twin = self._clone_bank()
            candidate, candidate_trace = self._run_transfers(
                twin,
                gateway=route,
                app_version=self.bank.current_app_version,
                runs=request.runs,
                run_id=f"ghost-rerun-{self._run_number:06d}",
            )
            control_bank = self._clone_bank()
            control, control_trace = self._run_transfers(
                control_bank,
                gateway="gateway_c",
                app_version=self.bank.current_app_version,
                runs=request.runs,
                run_id=f"ghost-rerun-{self._run_number:06d}-control",
            )
            failures = sum(result.status != TransferStatus.COMPLETED for result in candidate)
            control_failures = sum(result.status != TransferStatus.COMPLETED for result in control)
            passed = failures == 0 and control_failures == 0
            test_result = TestResult(
                id=f"ghost-rerun-{self._run_number:06d}-{hypothesis.id}",
                hypothesis_id=hypothesis.id,
                purpose=TestRunPurpose.POST_FIX_RERUN,
                test_plan=(
                    f"Rerun {request.runs} synthetic Bank X transfers through the current route "
                    f"({route}); all must complete and receive an acknowledgement."
                ),
                runs=request.runs,
                reproduced=failures,
                trace=(candidate_trace + control_trace)[:8],
                confidence=0.96 if passed else original.confidence,
                verdict=TestVerdict.VALIDATED if passed else TestVerdict.REJECTED,
                control=ControlResult(route="gateway_c.bank_x", runs=request.runs, reproduced=control_failures),
                disproof_passed=False,
                rerun_passed=passed,
            )
            await self._append_test_result(test_result)
            return RerunReceipt(test_result=test_result, route_used=route)

    def _run_hypothesis(self, hypothesis: Hypothesis, *, runs: int, run_id: str) -> TestResult:
        kind = self._classify(hypothesis.zone)
        if kind is None:
            return TestResult(
                id=f"{run_id}-{hypothesis.id}",
                hypothesis_id=hypothesis.id,
                test_plan=(
                    f"No isolated simulator check is registered for zone {hypothesis.zone}; "
                    "return this hypothesis for a testable disproof condition."
                ),
                runs=runs,
                reproduced=0,
                trace=[TraceStep(service="ghost-test", result="UNSUPPORTED_ZONE")],
                confidence=0.0,
                verdict=TestVerdict.INCONCLUSIVE,
                disproof_passed=None,
            )

        if kind == "gateway_ack":
            return self._test_gateway_ack(hypothesis, runs, run_id)
        if kind == "partner_bank":
            return self._test_partner_bank(hypothesis, runs, run_id)
        if kind == "app_release":
            return self._test_app_release(hypothesis, runs, run_id)
        return self._test_core_posting(hypothesis, runs, run_id)

    def _test_gateway_ack(self, hypothesis: Hypothesis, runs: int, run_id: str) -> TestResult:
        suspect_bank = self._clone_bank()
        suspect_bank.payment_router.routes["BANK_X"] = "gateway_b"
        control_bank = self._clone_bank()
        control_bank.payment_router.routes["BANK_X"] = "gateway_c"
        suspect, suspect_trace = self._run_transfers(
            suspect_bank, gateway="gateway_b", app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-suspect",
        )
        control, control_trace = self._run_transfers(
            control_bank, gateway="gateway_c", app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-control",
        )
        suspect_count = sum(result.status == TransferStatus.PENDING for result in suspect)
        control_count = sum(result.status == TransferStatus.PENDING for result in control)
        fault_active = ("gateway_b", "BANK_X") in self.bank.payment_router.acknowledgement_failures
        reproduced_rate = suspect_count / runs
        contrast = max(0.0, reproduced_rate - control_count / runs)
        fingerprint_match = 1.0 if fault_active and suspect_count > 0 else 0.0
        symptom_coverage = reproduced_rate
        disproof_passed = suspect_count == 0
        confidence = self._confidence(
            reproduced_rate, contrast, fingerprint_match, symptom_coverage, disproof_passed
        )
        return self._result(
            hypothesis,
            run_id,
            runs,
            suspect_count,
            control_count,
            "gateway_c.bank_x",
            suspect_trace + control_trace,
            confidence,
            disproof_passed,
            (
                f"Run {runs} synthetic Bank X transfers through Gateway B, then {runs} equivalent "
                "controls through healthy Gateway C; compare acknowledgements and destination credits."
            ),
        )

    def _test_partner_bank(self, hypothesis: Hypothesis, runs: int, run_id: str) -> TestResult:
        suspect_bank = self._clone_bank()
        suspect_gateway = self.bank.payment_router.route_for("BANK_X")
        control_bank = self._clone_bank()
        control_bank.payment_router.routes["BANK_X"] = "gateway_c"
        suspect, suspect_trace = self._run_transfers(
            suspect_bank, gateway=suspect_gateway, app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-suspect",
        )
        control, control_trace = self._run_transfers(
            control_bank, gateway="gateway_c", app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-control",
        )
        suspect_count = sum(not result.destination_credit_posted for result in suspect)
        control_count = sum(not result.destination_credit_posted for result in control)
        suspect_rate = suspect_count / runs
        control_rate = control_count / runs
        contrast = max(0.0, suspect_rate - control_rate)
        fingerprint_match = 1.0 if not self.bank.destination_banks["BANK_X"].available else 0.1
        symptom_coverage = suspect_rate if suspect_rate else 0.1
        disproof_passed = suspect_count == 0 and all(result.destination_credit_posted for result in control)
        confidence = self._confidence(
            suspect_rate, contrast, fingerprint_match, symptom_coverage, disproof_passed
        )
        return self._result(
            hypothesis,
            run_id,
            runs,
            suspect_count,
            control_count,
            "gateway_c.bank_x",
            suspect_trace + control_trace,
            confidence,
            disproof_passed,
            (
                f"Check {runs} Bank X credits on the suspect path and compare with {runs} transfers "
                "through Gateway C; separate destination credit acceptance from gateway acknowledgement."
            ),
        )

    def _test_app_release(self, hypothesis: Hypothesis, runs: int, run_id: str) -> TestResult:
        route = self.bank.payment_router.route_for("BANK_X")
        current_bank = self._clone_bank()
        previous_bank = self._clone_bank()
        current, current_trace = self._run_transfers(
            current_bank, gateway=route, app_version="4.2", runs=runs, run_id=f"{run_id}-v42"
        )
        previous, previous_trace = self._run_transfers(
            previous_bank, gateway=route, app_version="4.1", runs=runs, run_id=f"{run_id}-v41"
        )
        suspect_count = sum(result.status != TransferStatus.COMPLETED for result in current)
        control_count = sum(result.status != TransferStatus.COMPLETED for result in previous)
        suspect_rate = suspect_count / runs
        control_rate = control_count / runs
        contrast = max(0.0, suspect_rate - control_rate)
        disproof_passed = suspect_count > 0 and control_count == suspect_count
        fingerprint_match = 1.0 if suspect_rate > control_rate else 0.1
        symptom_coverage = 1.0 if suspect_rate > control_rate else 0.1
        confidence = self._confidence(
            suspect_rate, contrast, fingerprint_match, symptom_coverage, disproof_passed
        )
        return self._result(
            hypothesis,
            run_id,
            runs,
            suspect_count,
            control_count,
            f"mobile-app-v4.1 via {route}.bank_x",
            current_trace + previous_trace,
            confidence,
            disproof_passed,
            (
                f"Replay {runs} equivalent transfers through {route} on app versions 4.2 and 4.1; "
                "the release is implicated only if the symptom is exclusive to 4.2."
            ),
        )

    def _test_core_posting(self, hypothesis: Hypothesis, runs: int, run_id: str) -> TestResult:
        route = self.bank.payment_router.route_for("BANK_X")
        suspect_bank = self._clone_bank()
        control_bank = self._clone_bank()
        control_bank.payment_router.routes["BANK_X"] = "gateway_c"
        suspect, suspect_trace = self._run_transfers(
            suspect_bank, gateway=route, app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-suspect",
        )
        control, control_trace = self._run_transfers(
            control_bank, gateway="gateway_c", app_version=self.bank.current_app_version,
            runs=runs, run_id=f"{run_id}-control",
        )
        suspect_delays = self._core_delays(suspect_bank, suspect)
        control_delays = self._core_delays(control_bank, control)
        suspect_count = sum(delay > POSTING_SLO_MS for delay in suspect_delays)
        control_count = sum(delay > POSTING_SLO_MS for delay in control_delays)
        suspect_rate = suspect_count / runs
        control_rate = control_count / runs
        contrast = max(0.0, suspect_rate - control_rate)
        fingerprint_match = 1.0 if suspect_count > 0 else 0.1
        symptom_coverage = suspect_rate if suspect_rate else 0.1
        disproof_passed = suspect_count == 0
        confidence = self._confidence(
            suspect_rate, contrast, fingerprint_match, symptom_coverage, disproof_passed
        )
        return self._result(
            hypothesis,
            run_id,
            runs,
            suspect_count,
            control_count,
            "core-banking.control",
            suspect_trace + control_trace,
            confidence,
            disproof_passed,
            (
                f"Measure core posting latency for {runs} Bank X transfers against the {POSTING_SLO_MS} ms "
                "posting SLO, and compare with healthy Gateway C controls."
            ),
        )

    def _result(
        self,
        hypothesis: Hypothesis,
        run_id: str,
        runs: int,
        reproduced: int,
        control_reproduced: int,
        control_route: str,
        trace: list[TraceStep],
        confidence: float,
        disproof_passed: bool,
        test_plan: str,
    ) -> TestResult:
        verdict = (
            TestVerdict.VALIDATED
            if confidence >= HANDOFF_THRESHOLD and not disproof_passed
            else TestVerdict.REJECTED
            if disproof_passed or confidence < 0.25
            else TestVerdict.INCONCLUSIVE
        )
        return TestResult(
            id=f"{run_id}-{hypothesis.id}",
            hypothesis_id=hypothesis.id,
            test_plan=test_plan,
            runs=runs,
            reproduced=reproduced,
            trace=trace[:8],
            confidence=confidence,
            verdict=verdict,
            control=ControlResult(route=control_route, runs=runs, reproduced=control_reproduced),
            disproof_passed=disproof_passed,
        )

    def _run_transfers(
        self,
        twin: SimulatedBank,
        *,
        gateway: str,
        app_version: str,
        runs: int,
        run_id: str,
    ) -> tuple[list[TransferResult], list[TraceStep]]:
        twin.payment_router.routes["BANK_X"] = gateway
        twin.current_app_version = app_version
        results = [
            twin.transfer(
                transaction_id=f"{run_id}-txn-{index + 1:02d}",
                customer_token=f"cust_ghost_{run_id}_{index + 1:02d}",
                destination_bank="BANK_X",
                amount_ngn="1000.00",
                channel="mobile",
                app_version=app_version,
            )
            for index in range(runs)
        ]
        trace = self._trace_for_transaction(twin, results[0].transaction_id)
        return results, trace

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
    def _trace_for_transaction(bank: SimulatedBank, transaction_id: str) -> list[TraceStep]:
        trace: list[TraceStep] = []
        for event in bank.events:
            if event.payload.get("transaction_id") != transaction_id:
                continue
            trace.append(
                TraceStep(
                    service=event.service,
                    result=event.code or "EVENT",
                    latency_ms=event.latency_ms,
                    error_code=event.code if event.code and "TIMEOUT" in event.code else None,
                )
            )
        return trace

    @staticmethod
    def _core_delays(bank: SimulatedBank, results: list[TransferResult]) -> list[int]:
        transaction_ids = {result.transaction_id for result in results}
        return [
            event.latency_ms
            for event in bank.events
            if event.service == "core-banking"
            and event.payload.get("transaction_id") in transaction_ids
            and event.latency_ms is not None
        ]

    @staticmethod
    def _classify(zone: str) -> Literal["gateway_ack", "partner_bank", "app_release", "core_posting"] | None:
        normalized = zone.lower().replace("-", "_")
        if "gateway_b" in normalized or "connector" in normalized:
            return "gateway_ack"
        if "partner_bank" in normalized or "partner" in normalized:
            return "partner_bank"
        if "mobile_app" in normalized or "release" in normalized:
            return "app_release"
        if "core_banking" in normalized or "posting" in normalized:
            return "core_posting"
        return None

    @staticmethod
    def _confidence(
        reproduction: float,
        control_contrast: float,
        fingerprint_match: float,
        symptom_coverage: float,
        disproof_passed: bool,
    ) -> float:
        disproof_support = 0.0 if disproof_passed else 1.0
        score = (
            0.20 * reproduction
            + 0.15 * control_contrast
            + 0.25 * fingerprint_match
            + 0.15 * symptom_coverage
            + 0.25 * disproof_support
        )
        return round(min(0.99, score * CONFIDENCE_CALIBRATION), 2)

    async def _append_result_pair(self, hypothesis: Hypothesis, test_result: TestResult) -> None:
        await self.event_stream.ingest(
            "hypothesis",
            hypothesis.model_dump(mode="json"),
            occurred_at=self._next_record_time(),
        )
        await self._append_test_result(test_result)

    async def _append_test_result(self, test_result: TestResult) -> None:
        await self.event_stream.ingest(
            "test_result",
            test_result.model_dump(mode="json"),
            occurred_at=self._next_record_time(),
        )

    def _next_record_time(self) -> datetime:
        self._record_number += 1
        return datetime.now(timezone.utc) + timedelta(microseconds=self._record_number)


__all__ = [
    "HANDOFF_THRESHOLD",
    "GhostTestAgent",
    "HypothesisEvaluationSet",
    "HypothesisSubmission",
    "RerunReceipt",
    "RerunNotReadyError",
    "RerunRequest",
    "UnknownHypothesisError",
]
