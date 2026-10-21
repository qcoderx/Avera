"""Transactional event dispatch behavior exercised with the in-memory adapter."""

import asyncio
from datetime import datetime, timezone

from avera.events.bus import InMemoryEventBus
from avera.events.repository import InMemoryEventRepository
from avera.events.service import EventStreamService


class FlakyBus(InMemoryEventBus):
    def __init__(self, *, publish_failures=0, fail_dead_letter=False):
        super().__init__()
        self.publish_failures_remaining = publish_failures
        self.fail_dead_letter = fail_dead_letter

    async def publish(self, record):
        if self.publish_failures_remaining:
            self.publish_failures_remaining -= 1
            raise ConnectionError("broker unavailable")
        await super().publish(record)

    async def dead_letter(self, record, reason):
        if self.fail_dead_letter:
            raise ConnectionError("dead-letter topic unavailable")
        await super().dead_letter(record, reason)


def payload(event_id="evt-1"):
    return {
        "id": event_id,
        "ts": datetime(2026, 10, 21, tzinfo=timezone.utc).isoformat(),
        "source": "test",
        "channel": "api",
        "service": "gateway_b",
        "code": "ACK_TIMEOUT",
    }


def test_published_event_is_idempotent_and_clears_outbox():
    async def scenario():
        repository = InMemoryEventRepository()
        bus = FlakyBus()
        service = EventStreamService(repository, bus)

        first = await service.ingest("event", payload())
        duplicate = await service.ingest("event", payload())

        assert first.delivery == "published"
        assert duplicate.created is False and duplicate.delivery == "published"
        assert bus.published_count == 1
        assert await repository.outbox_pending_count() == 0

    asyncio.run(scenario())


def test_publish_and_dlq_failure_keeps_event_pending_for_retry():
    async def scenario():
        repository = InMemoryEventRepository()
        bus = FlakyBus(publish_failures=3, fail_dead_letter=True)
        service = EventStreamService(repository, bus)

        outcome = await service.ingest("event", payload())
        assert outcome.delivery == "durable_unpublished"
        assert await repository.outbox_pending_count() == 1

        await service.flush_outbox()
        assert await repository.outbox_pending_count() == 0
        assert await repository.outbox_status(outcome.record.sequence) == "published"
        assert bus.published_count == 1

    asyncio.run(scenario())


def test_successful_dead_letter_marks_terminal_outbox_state():
    async def scenario():
        repository = InMemoryEventRepository()
        bus = FlakyBus(publish_failures=3)
        service = EventStreamService(repository, bus)

        outcome = await service.ingest("event", payload())
        assert outcome.delivery == "dead_lettered"
        assert await repository.outbox_status(outcome.record.sequence) == "dead_lettered"
        assert await repository.outbox_pending_count() == 0
        assert len(bus.dead_letters) == 1

    asyncio.run(scenario())


def test_bulk_ingestion_preserves_order_and_defers_delivery():
    async def scenario():
        repository = InMemoryEventRepository()
        bus = FlakyBus()
        service = EventStreamService(repository, bus)

        outcomes = await service.ingest_many([
            ("event", payload("bulk-1")),
            ("event", payload("bulk-2")),
            ("event", payload("bulk-1")),
        ])

        assert [item.record.sequence for item in outcomes] == [1, 2, 1]
        assert [item.created for item in outcomes] == [True, True, False]
        assert bus.published_count == 0
        assert await repository.outbox_pending_count() == 2

        assert await service.flush_outbox() == 2
        assert bus.published_count == 2
        assert await repository.outbox_pending_count() == 0

    asyncio.run(scenario())
