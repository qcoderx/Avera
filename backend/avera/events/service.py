"""Typed ingestion, durable sequencing, fixture replay, and SSE read support."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from avera.contracts import ContractObjectType, IncidentFixture, StreamRecord
from avera.events.bus import EventBus
from avera.events.normalizer import normalize_record
from avera.events.repository import EventRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    record: StreamRecord
    created: bool
    delivery: str


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    scenario: str
    records: int
    inserted: int
    first_sequence: int | None
    last_sequence: int | None


class EventStreamService:
    def __init__(self, repository: EventRepository, bus: EventBus) -> None:
        self.repository = repository
        self.bus = bus
        self._outbox_flush_lock = asyncio.Lock()
        self.observation_handlers = []
        self.metrics = {
            "ingested": 0,
            "duplicates": 0,
            "publish_failures": 0,
            "dead_lettered": 0,
            "unpublished": 0,
            "outbox_dispatch_failures": 0,
        }

    async def ingest(
        self,
        object_type: ContractObjectType | str,
        payload: dict[str, Any],
        *,
        occurred_at=None,
        source: str | None = None,
    ) -> IngestOutcome:
        record = normalize_record(
            object_type,
            payload,
            occurred_at=occurred_at,
            source=source,
        )
        stored, created = await self.repository.append(record)
        if not created:
            self.metrics["duplicates"] += 1
        else:
            self.metrics["ingested"] += 1
        # Optional synchronous consumers inspect only validated durable records.
        # Delivery is idempotent at each consumer, including duplicate ingestion.
        for handler in self.observation_handlers:
            try:
                handler(stored.type.value, stored.data)
            except Exception:
                # An optional investigator must not block infrastructure delivery.
                self.metrics["observation_failures"] = self.metrics.get("observation_failures", 0) + 1
        await self.flush_outbox()
        status = await self.repository.outbox_status(stored.sequence)
        delivery = {
            "published": "published",
            "dead_lettered": "dead_lettered",
            "pending": "durable_unpublished",
        }.get(status, "durable_unpublished")
        return IngestOutcome(stored, created, delivery)

    async def flush_outbox(self, *, limit: int = 100) -> int:
        """Publish committed events; a process crash is recovered from the outbox."""
        async with self._outbox_flush_lock:
            items = await self.repository.claim_outbox(limit=limit)
            for item in items:
                record = item.record
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        await self.bus.publish(record)
                        await self.repository.finish_outbox(record.sequence)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        self.metrics["publish_failures"] += 1
                        if attempt < 2:
                            await asyncio.sleep(0.05 * (2**attempt))
                if last_error is None:
                    continue

                reason = f"publish failed after 3 attempts: {type(last_error).__name__}: {last_error}"
                try:
                    await self.bus.dead_letter(record, reason)
                    await self.repository.finish_outbox(
                        record.sequence, dead_lettered=True, error=reason
                    )
                    self.metrics["dead_lettered"] += 1
                except Exception as dlq_error:
                    retry_error = (
                        f"publish failed ({type(last_error).__name__}); "
                        f"dead-letter failed ({type(dlq_error).__name__})"
                    )
                    await self.repository.finish_outbox(record.sequence, error=retry_error)
                    self.metrics["unpublished"] += 1
                    self.metrics["outbox_dispatch_failures"] += 1
                    logger.exception("Event is durable but Kafka publish and DLQ both failed")
            return len(items)

    async def records_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]:
        return await self.repository.list_after(sequence, limit=limit)

    async def replay(self, fixture_path: Path | None = None) -> ReplaySummary:
        path = fixture_path or Path(__file__).resolve().parents[1] / "fixtures" / "full_incident_stream.json"
        fixture = IncidentFixture.model_validate_json(path.read_text(encoding="utf-8"))
        outcomes: list[IngestOutcome] = []
        for fixture_record in fixture.records:
            outcomes.append(
                await self.ingest(
                    fixture_record.type,
                    fixture_record.data,
                    occurred_at=fixture_record.occurred_at,
                )
            )
        sequences = [outcome.record.sequence for outcome in outcomes]
        return ReplaySummary(
            scenario=fixture.scenario,
            records=len(outcomes),
            inserted=sum(outcome.created for outcome in outcomes),
            first_sequence=min(sequences) if sequences else None,
            last_sequence=max(sequences) if sequences else None,
        )

    def stream_queue(self):
        return self.bus.subscribe()

    def close_stream(self, queue) -> None:
        self.bus.unsubscribe(queue)


def encode_sse(record: StreamRecord) -> str:
    data = json.dumps(record.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {record.sequence}\nevent: {record.type.value}\ndata: {data}\n\n"


__all__ = ["EventStreamService", "IngestOutcome", "ReplaySummary", "encode_sse"]
