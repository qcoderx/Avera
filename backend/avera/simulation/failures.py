"""Deterministic demo failure and decoy release injection."""

from __future__ import annotations

from dataclasses import dataclass

from avera.contracts import Event
from avera.simulation.bank import SimulatedBank


@dataclass(frozen=True, slots=True)
class FailureInjectionResult:
    gateway: str
    destination_bank: str
    app_version: str
    release_event: Event
    failure_event: Event


class FailureInjector:
    """Enables the Bank X/Gateway B ack fault and emits the app-release decoy."""

    def __init__(
        self,
        bank: SimulatedBank,
        *,
        release_version: str = "4.2",
        release_id: str = "rel-demo-42",
    ) -> None:
        self.bank = bank
        self.release_version = release_version
        self.release_id = release_id
        self._result: FailureInjectionResult | None = None

    def inject(self) -> FailureInjectionResult:
        """Apply the same fault once; repeated calls return its original evidence."""
        if self._result is not None:
            return self._result

        gateway = "gateway_b"
        destination_bank = "BANK_X"
        self.bank.payment_router.set_acknowledgement_failure(gateway, destination_bank)
        self.bank.current_app_version = self.release_version
        release_event = self.bank.emit_event(
            source="release-webhook",
            channel="internal",
            service="mobile-app",
            route="release",
            code="APP_RELEASED",
            payload={
                "version": self.release_version,
                "environment": "simulated-production",
                "change_id": self.release_id,
                "decoy": True,
            },
        )
        failure_event = self.bank.emit_event(
            source="demo-failure-injector",
            channel="internal",
            service=gateway,
            route=f"{gateway}.{destination_bank.lower()}",
            code="ACK_SUPPRESSION_ENABLED",
            payload={
                "gateway": gateway,
                "destination_bank": destination_bank,
                "fault": "successful destination response acknowledgement is dropped",
            },
        )
        self._result = FailureInjectionResult(
            gateway=gateway,
            destination_bank=destination_bank,
            app_version=self.release_version,
            release_event=release_event,
            failure_event=failure_event,
        )
        return self._result

    def reset(self) -> None:
        self.bank.reset()
        self._result = None
