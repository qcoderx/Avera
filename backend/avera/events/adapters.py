"""Adapters that bridge synchronous simulator/webhook callbacks into async ingestion."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from avera.contracts import Complaint, ContractObjectType, Event
from avera.events.service import EventStreamService

logger = logging.getLogger(__name__)


class SimulationIngestionAdapter:
    """Preserve simulator callback order while handing records to the async pipeline."""

    def __init__(self, service: EventStreamService) -> None:
        self.service = service
        self._queue: asyncio.Queue[tuple[ContractObjectType, dict[str, Any]] | None] = asyncio.Queue()
        self._early: list[tuple[ContractObjectType, dict[str, Any]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task | None = None
        self.failures = 0

    def on_event(self, event: Event) -> None:
        self._enqueue(ContractObjectType.EVENT, event.model_dump(mode="json"))

    def on_complaint(self, complaint: Complaint) -> None:
        self._enqueue(ContractObjectType.COMPLAINT, complaint.model_dump(mode="json"))

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._worker = asyncio.create_task(self._run(), name="avera-simulator-ingestion")
        for item in self._early:
            self._queue.put_nowait(item)
        self._early.clear()

    async def close(self) -> None:
        if self._worker is None:
            return
        await self._queue.join()
        self._queue.put_nowait(None)
        await self._worker
        self._worker = None
        self._loop = None

    def _enqueue(self, kind: ContractObjectType, payload: dict[str, Any]) -> None:
        item = (kind, payload)
        loop = self._loop
        if loop is None:
            self._early.append(item)
            return
        if loop.is_running():
            try:
                caller_loop = asyncio.get_running_loop()
            except RuntimeError:
                caller_loop = None
            if caller_loop is loop:
                self._queue.put_nowait(item)
            else:
                loop.call_soon_threadsafe(self._queue.put_nowait, item)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                kind, payload = item
                await self.service.ingest(kind, payload)
            except Exception:
                self.failures += 1
                logger.exception("Failed to ingest simulator callback")
            finally:
                self._queue.task_done()


__all__ = ["SimulationIngestionAdapter"]
