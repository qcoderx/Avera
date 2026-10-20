"""FastAPI application for typed ingestion, service-map reads, and SSE replay."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from avera.contracts import ContractObjectType
from avera.events.adapters import SimulationIngestionAdapter
from avera.events.bus import EventBus, InMemoryEventBus, KafkaRedpandaEventBus
from avera.events.repository import EventRepository, InMemoryEventRepository, PostgresEventRepository
from avera.events.service import EventStreamService, IngestOutcome, ReplaySummary, encode_sse
from avera.ghost_test import (
    GhostTestAgent,
    HypothesisEvaluationSet,
    HypothesisSubmission,
    RerunReceipt,
    RerunNotReadyError,
    RerunRequest,
    UnknownHypothesisError,
)
from avera.simulation.bank import SimulatedBank


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ContractObjectType
    data: dict[str, Any]
    occurred_at: datetime | None = None


class IngestReceipt(BaseModel):
    record: dict[str, Any]
    created: bool
    delivery: str


class ReplayReceipt(BaseModel):
    scenario: str
    records: int
    inserted: int
    first_sequence: int | None
    last_sequence: int | None


class RuntimeConfigurationError(RuntimeError):
    pass


def _runtime_adapters() -> tuple[EventRepository, EventBus]:
    database_url = os.getenv("AVERA_DATABASE_URL")
    kafka_servers = os.getenv("AVERA_KAFKA_BOOTSTRAP_SERVERS")
    if bool(database_url) != bool(kafka_servers):
        raise RuntimeConfigurationError(
            "Configure both AVERA_DATABASE_URL and AVERA_KAFKA_BOOTSTRAP_SERVERS, or neither"
        )
    if database_url and kafka_servers:
        return PostgresEventRepository(database_url), KafkaRedpandaEventBus(kafka_servers)
    return InMemoryEventRepository(), InMemoryEventBus()


def create_app(
    *,
    repository: EventRepository | None = None,
    bus: EventBus | None = None,
    bank: SimulatedBank | None = None,
) -> FastAPI:
    if repository is None and bus is None:
        repository, bus = _runtime_adapters()
    elif repository is None or bus is None:
        raise ValueError("repository and bus must be injected together")

    assert repository is not None and bus is not None
    service = EventStreamService(repository, bus)
    simulator_adapter = SimulationIngestionAdapter(service)
    simulated_bank = bank or SimulatedBank()
    simulated_bank.set_event_sinks(
        event_sink=simulator_adapter.on_event,
        complaint_sink=simulator_adapter.on_complaint,
    )
    ghost_test_agent = GhostTestAgent(simulated_bank, service)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await repository.start()
        try:
            await bus.start()
            await simulator_adapter.start()
            yield
        finally:
            await simulator_adapter.close()
            await bus.close()
            await repository.close()

    app = FastAPI(
        title="Avera Event API",
        version="1.0.0",
        description="Typed event ingestion and ordered, replayable incident streams.",
        lifespan=lifespan,
    )
    app.state.event_service = service
    app.state.event_repository = repository
    app.state.event_bus = bus
    app.state.simulated_bank = simulated_bank
    app.state.simulator_ingestion = simulator_adapter
    app.state.ghost_test_agent = ghost_test_agent

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "storage": "postgresql" if isinstance(repository, PostgresEventRepository) else "memory",
            "event_bus": "kafka" if isinstance(bus, KafkaRedpandaEventBus) else "memory",
            "simulator_queue_depth": simulator_adapter._queue.qsize(),
            "simulator_adapter_failures": simulator_adapter.failures,
            "metrics": dict(service.metrics),
        }

    @app.get("/service-map")
    async def service_map() -> dict[str, object]:
        return simulated_bank.service_map()

    @app.post("/ingest/{source}", response_model=IngestReceipt, status_code=202)
    async def ingest(source: str, body: IngestRequest, response: Response) -> IngestReceipt:
        if not source or len(source) > 64 or not all(char.isalnum() or char in "._-" for char in source):
            raise HTTPException(status_code=422, detail="source must contain only letters, numbers, '.', '_' or '-'")
        try:
            outcome: IngestOutcome = await service.ingest(
                body.type,
                body.data,
                occurred_at=body.occurred_at,
                source=source,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response.headers["X-Avera-Sequence"] = str(outcome.record.sequence)
        response.headers["X-Avera-Delivery"] = outcome.delivery
        return IngestReceipt(
            record=outcome.record.model_dump(mode="json"),
            created=outcome.created,
            delivery=outcome.delivery,
        )

    @app.post("/replay", response_model=ReplayReceipt, status_code=202)
    async def replay() -> ReplayReceipt:
        summary: ReplaySummary = await service.replay()
        return ReplayReceipt(
            scenario=summary.scenario,
            records=summary.records,
            inserted=summary.inserted,
            first_sequence=summary.first_sequence,
            last_sequence=summary.last_sequence,
        )

    @app.post("/hypotheses", response_model=HypothesisEvaluationSet, status_code=202)
    async def submit_hypotheses(body: HypothesisSubmission) -> HypothesisEvaluationSet:
        return await ghost_test_agent.evaluate(body)

    @app.post("/ghost-test/rerun", response_model=RerunReceipt, status_code=202)
    async def ghost_test_rerun(body: RerunRequest) -> RerunReceipt:
        try:
            return await ghost_test_agent.rerun(body)
        except (UnknownHypothesisError, RerunNotReadyError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"No previous Ghost Test run is available for hypothesis {exc.args[0]!r}",
            ) from exc

    @app.get("/stream")
    async def stream(
        request: Request,
        after: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        cursor = after or 0
        if last_event_id is not None:
            try:
                cursor = int(last_event_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Last-Event-ID must be a non-negative integer") from exc
            if cursor < 0:
                raise HTTPException(status_code=400, detail="Last-Event-ID must be a non-negative integer")

        stream_cursor = [cursor]

        async def generate():
            queue = service.stream_queue()
            try:
                while not await request.is_disconnected():
                    records = await service.records_after(stream_cursor[0])
                    if records:
                        for record in records:
                            if record.sequence <= stream_cursor[0]:
                                continue
                            yield encode_sse(record)
                            stream_cursor[0] = record.sequence
                        continue
                    try:
                        await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                service.close_stream(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()

__all__ = ["app", "create_app"]
