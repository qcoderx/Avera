"""Event notification buses: local fan-out for demos and Kafka/Redpanda in prod."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from typing import Any, Protocol
from uuid import uuid4

from avera.contracts import StreamRecord

logger = logging.getLogger(__name__)


class EventBus(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, record: StreamRecord) -> None: ...

    async def dead_letter(self, record: StreamRecord, reason: str) -> None: ...

    def subscribe(self) -> asyncio.Queue[None]: ...

    def unsubscribe(self, queue: asyncio.Queue[None]) -> None: ...


class _SubscriberFanout:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[None]] = set()

    def subscribe(self) -> asyncio.Queue[None]:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[None]) -> None:
        self._subscribers.discard(queue)

    def notify(self) -> None:
        # The queue is only a wake-up signal; SSE reads ordered rows from storage.
        for queue in tuple(self._subscribers):
            if queue.empty():
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)


class InMemoryEventBus(_SubscriberFanout):
    """Local notifier with inspectable dead letters for development and demos."""

    def __init__(self) -> None:
        super().__init__()
        self.dead_letters: list[dict[str, object]] = []
        self.published_count = 0

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def publish(self, record: StreamRecord) -> None:
        self.published_count += 1
        self.notify()

    async def dead_letter(self, record: StreamRecord, reason: str) -> None:
        self.dead_letters.append({"record": record.model_dump(mode="json"), "reason": reason})
        self.notify()


class KafkaRedpandaEventBus(_SubscriberFanout):
    """Kafka-compatible notifier; configure a single-bank keyed stream topic.

    PostgreSQL remains the replay authority. Kafka messages wake local SSE
    subscribers, which always read in sequence order from the durable log.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        topic: str = "avera.stream.v1",
        dead_letter_topic: str = "avera.stream.dlq.v1",
        security_protocol: str = "PLAINTEXT",
        sasl_mechanism: str | None = None,
        sasl_username: str | None = None,
        sasl_password: str | None = None,
        ca_file: str | None = None,
    ) -> None:
        super().__init__()
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.dead_letter_topic = dead_letter_topic
        self.security_protocol = security_protocol.strip().upper()
        self.sasl_mechanism = sasl_mechanism.strip() if sasl_mechanism else None
        self.sasl_username = sasl_username
        self.sasl_password = sasl_password
        self.ca_file = ca_file
        self._producer = None
        self._consumer = None
        self._consumer_task: asyncio.Task | None = None
        self.published_count = 0
        self.dead_letter_count = 0

    def _client_options(self) -> dict[str, Any]:
        if self.security_protocol == "PLAINTEXT":
            return {"security_protocol": self.security_protocol}
        if self.security_protocol != "SASL_SSL":
            raise ValueError("Kafka security protocol must be PLAINTEXT or SASL_SSL")
        if not all((self.sasl_mechanism, self.sasl_username, self.sasl_password)):
            raise ValueError("SASL_SSL requires a mechanism, username, and password")
        if not self.ca_file:
            raise ValueError("SASL_SSL requires a CA certificate file")
        return {
            "security_protocol": self.security_protocol,
            "sasl_mechanism": self.sasl_mechanism,
            "sasl_plain_username": self.sasl_username,
            "sasl_plain_password": self.sasl_password,
            "ssl_context": ssl.create_default_context(cafile=self.ca_file),
        }

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        except ImportError as exc:  # pragma: no cover - exercised in configured deployments
            raise RuntimeError("Install aiokafka to enable Kafka/Redpanda integration") from exc

        client_options = self._client_options()
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            **client_options,
        )
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=f"avera-api-{uuid4().hex}",
            auto_offset_reset="latest",
            enable_auto_commit=True,
            **client_options,
        )
        await self._producer.start()
        try:
            await self._consumer.start()
        except Exception:
            await self._producer.stop()
            self._producer = None
            raise
        self._consumer_task = asyncio.create_task(self._consume(), name="avera-kafka-notifier")

    async def close(self) -> None:
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
            self._consumer_task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, record: StreamRecord) -> None:
        producer = self._require_producer()
        await producer.send_and_wait(
            self.topic,
            json.dumps(record.model_dump(mode="json"), separators=(",", ":")).encode(),
            key=b"bank:demo",
        )
        self.published_count += 1

    async def dead_letter(self, record: StreamRecord, reason: str) -> None:
        producer = self._require_producer()
        message = {
            "reason": reason,
            "record": record.model_dump(mode="json"),
        }
        await producer.send_and_wait(
            self.dead_letter_topic,
            json.dumps(message, separators=(",", ":")).encode(),
            key=b"bank:demo",
        )
        self.dead_letter_count += 1

    async def _consume(self) -> None:
        assert self._consumer is not None
        async for message in self._consumer:
            try:
                StreamRecord.model_validate_json(message.value)
            except Exception:
                logger.exception("Ignoring malformed canonical notification from Kafka")
                continue
            self.notify()

    def _require_producer(self):
        if self._producer is None:
            raise RuntimeError("Kafka event bus has not been started")
        return self._producer


__all__ = ["EventBus", "InMemoryEventBus", "KafkaRedpandaEventBus"]
