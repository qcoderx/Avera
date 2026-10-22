"""FastAPI application for typed ingestion, service-map reads, and SSE replay."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from avera.contracts import ContractObjectType
from avera.control import (
    ControlPlane,
    DemoInjectRequest,
    TwinSimulationReceipt,
    TwinSimulationRequest,
)
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
from avera.llm import GhostSummaryProvider, configured_test_summary_provider
from avera.simulation.bank import SimulatedBank
from avera.observability.model import configured_model
from avera.observability.orchestration import ObservabilityAgent
from avera.observability.replay import replay_observations
from avera.api.observe import (
    OperationalLogHandler,
    OperationalLogStore,
    build_observe_router,
    install_log_capture,
    remove_log_capture,
)
from avera.remediation.approvals.api import build_router as build_approval_router
from avera.remediation.integration import RemediationRuntime, build_runtime

logger = logging.getLogger(__name__)


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


def _cors_origins() -> list[str]:
    """Return validated, exact browser origins from the deployment environment."""

    configured = os.getenv("AVERA_CORS_ORIGINS", "")
    origins: list[str] = []
    for value in configured.split(","):
        origin = value.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeConfigurationError(
                "AVERA_CORS_ORIGINS must contain exact http(s) origins without paths"
            )
        if origin not in origins:
            origins.append(origin)
    return origins


def _runtime_adapters() -> tuple[EventRepository, EventBus]:
    database_url = os.getenv("AVERA_DATABASE_URL")
    kafka_servers = os.getenv("AVERA_KAFKA_BOOTSTRAP_SERVERS")
    if bool(database_url) != bool(kafka_servers):
        raise RuntimeConfigurationError(
            "Configure both AVERA_DATABASE_URL and AVERA_KAFKA_BOOTSTRAP_SERVERS, or neither"
        )
    if database_url and kafka_servers:
        return PostgresEventRepository(database_url), KafkaRedpandaEventBus(
            kafka_servers,
            security_protocol=os.getenv("AVERA_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
            sasl_mechanism=os.getenv("AVERA_KAFKA_SASL_MECHANISM"),
            sasl_username=os.getenv("AVERA_KAFKA_SASL_USERNAME"),
            sasl_password=os.getenv("AVERA_KAFKA_SASL_PASSWORD"),
            ca_file=os.getenv("AVERA_KAFKA_CA_FILE"),
        )
    return InMemoryEventRepository(), InMemoryEventBus()


def create_app(
    *,
    repository: EventRepository | None = None,
    bus: EventBus | None = None,
    bank: SimulatedBank | None = None,
    test_summary_provider: GhostSummaryProvider | None = None,
    observability_model=None,
) -> FastAPI:
    if repository is None and bus is None:
        repository, bus = _runtime_adapters()
    elif repository is None or bus is None:
        raise ValueError("repository and bus must be injected together")

    assert repository is not None and bus is not None
    service = EventStreamService(repository, bus)
    operational_logs = OperationalLogStore()
    operational_log_handler = OperationalLogHandler(operational_logs)
    simulator_adapter = SimulationIngestionAdapter(service)
    simulated_bank = bank or SimulatedBank()
    simulated_bank.set_event_sinks(
        event_sink=simulator_adapter.on_event,
        complaint_sink=simulator_adapter.on_complaint,
    )
    summary_provider = (
        test_summary_provider
        if test_summary_provider is not None
        else configured_test_summary_provider()
    )
    ghost_test_agent = GhostTestAgent(simulated_bank, service, summary_provider)
    language_model = observability_model if observability_model is not None else configured_model()
    observability = ObservabilityAgent(service, ghost_test_agent, simulated_bank.service_map, language_model)
    service.observation_handlers.append(observability.observe)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        install_log_capture(operational_log_handler)
        operational_logs.append(
            "INFO",
            "avera.runtime",
            "Avera API process starting",
            kind="lifecycle",
        )
        repository_started = False
        bus_started = False
        simulator_started = False
        try:
            await repository.start()
            repository_started = True
            restored_case_id = await restore_remediation_state()
            if restored_case_id is not None:
                operational_logs.append(
                    "INFO",
                    "avera.remediation",
                    "Durable remediation state restored",
                    kind="lifecycle",
                )
            operational_logs.append(
                "INFO",
                "avera.infrastructure",
                "PostgreSQL connection established"
                if isinstance(repository, PostgresEventRepository)
                else "In-memory event repository ready",
                kind="infrastructure",
            )
            await bus.start()
            bus_started = True
            if isinstance(bus, KafkaRedpandaEventBus):
                provider = "Aiven Kafka" if "aivencloud.com" in bus.bootstrap_servers else "Kafka"
                operational_logs.append(
                    "INFO",
                    "avera.infrastructure",
                    f"{provider} producer and consumer connected",
                    kind="infrastructure",
                )
            await simulator_adapter.start()
            simulator_started = True
            app.state.outbox_task = asyncio.create_task(dispatch_outbox(), name="avera-event-outbox")
            operational_logs.append(
                "INFO",
                "avera.runtime",
                "Avera API is ready to receive traffic",
                kind="lifecycle",
            )
            yield
        finally:
            outbox_task = getattr(app.state, "outbox_task", None)
            if outbox_task is not None:
                outbox_task.cancel()
                await asyncio.gather(outbox_task, return_exceptions=True)
                app.state.outbox_task = None
            task = getattr(app.state, "observability_task", None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if simulator_started:
                await simulator_adapter.close()
            if language_model is not None and hasattr(language_model, "close"):
                await language_model.close()
            if summary_provider is not None and hasattr(summary_provider, "close"):
                await summary_provider.close()
            if bus_started:
                await bus.close()
            if repository_started:
                await repository.close()
            operational_logs.append(
                "INFO",
                "avera.runtime",
                "Avera API process stopped",
                kind="lifecycle",
            )
            remove_log_capture(operational_log_handler)

    async def dispatch_outbox() -> None:
        while True:
            try:
                await service.flush_outbox()
            except Exception as exc:
                service.metrics["outbox_dispatch_failures"] += 1
                logger.error("Outbox dispatcher failed (%s)", type(exc).__name__)
            await asyncio.sleep(1)

    app = FastAPI(
        title="Avera Event API",
        version="1.0.0",
        description="Typed event ingestion and ordered, replayable incident streams.",
        lifespan=lifespan,
    )
    cors_origins = _cors_origins()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Last-Event-ID"],
            expose_headers=[
                "X-Avera-Request-ID",
                "X-Avera-Sequence",
                "X-Avera-Delivery",
            ],
            max_age=600,
        )
    app.state.event_service = service
    app.state.operational_logs = operational_logs
    app.state.event_repository = repository
    app.state.event_bus = bus
    app.state.simulated_bank = simulated_bank
    app.state.simulator_ingestion = simulator_adapter
    app.state.ghost_test_agent = ghost_test_agent
    app.state.ghost_test_summary_provider = summary_provider
    app.state.observability_agent = observability
    app.state.observability_task = None
    app.state.outbox_task = None
    remediation_runtime: RemediationRuntime = build_runtime(simulated_bank)
    app.state.remediation_runtime = remediation_runtime
    app.state.active_remediation_case_id = None
    app.state.remediation_error = None
    remediation_state_lock = asyncio.Lock()

    async def restore_remediation_state() -> str | None:
        snapshot = await repository.load_runtime_state("remediation.v1")
        if snapshot is None:
            return None
        active_case_id = remediation_runtime.restore(snapshot)
        app.state.active_remediation_case_id = active_case_id
        return active_case_id

    async def persist_remediation_state() -> None:
        async with remediation_state_lock:
            snapshot = remediation_runtime.snapshot(app.state.active_remediation_case_id)
            await repository.save_runtime_state("remediation.v1", snapshot)

    async def after_approval_decision() -> None:
        await remediation_runtime.flush_events(service)
        await persist_remediation_state()

    @app.middleware("http")
    async def operational_request_log(request: Request, call_next):
        started = time.perf_counter()
        request_id = uuid4().hex[:12]
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            if not path.startswith("/observe/api/"):
                operational_logs.append(
                    "ERROR",
                    "avera.http",
                    f"{request.method} {path} failed",
                    kind="request",
                    method=request.method,
                    path=path,
                    status=500,
                    duration_ms=round((time.perf_counter() - started) * 1_000, 1),
                    request_id=request_id,
                )
            raise

        if not path.startswith("/observe/api/"):
            level = "ERROR" if response.status_code >= 500 else (
                "WARNING" if response.status_code >= 400 else "INFO"
            )
            operational_logs.append(
                level,
                "avera.http",
                f"{request.method} {path} completed with {response.status_code}",
                kind="request",
                method=request.method,
                path=path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1_000, 1),
                request_id=request_id,
            )
        response.headers["X-Avera-Request-ID"] = request_id
        return response

    investigation_requested = False
    investigation_lock = asyncio.Lock()

    async def process_observations():
        async with investigation_lock:
            await simulator_adapter.flush()
            result = await observability.run()
            try:
                handoff = remediation_runtime.make_handoff(observability)
                if handoff is not None:
                    case = remediation_runtime.remediation.on_validated(handoff)
                    app.state.active_remediation_case_id = case.incident_id
                    app.state.remediation_error = None
            except Exception as exc:
                app.state.remediation_error = f"handoff_failed:{type(exc).__name__}"
            await remediation_runtime.flush_events(service)
            await persist_remediation_state()
            return result

    async def drain_investigation_requests():
        nonlocal investigation_requested
        while investigation_requested:
            investigation_requested = False
            await process_observations()

    def schedule_investigation():
        nonlocal investigation_requested
        investigation_requested = True
        task = app.state.observability_task
        if task is None or task.done():
            app.state.observability_task = asyncio.create_task(drain_investigation_requests())

    async def stop_investigation():
        nonlocal investigation_requested
        task = app.state.observability_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        app.state.observability_task = None
        investigation_requested = False

    control_plane = ControlPlane(
        simulated_bank,
        service,
        ghost_test_agent,
        simulator_adapter.flush,
    )
    app.state.control_plane = control_plane
    async def health_snapshot() -> dict[str, Any]:
        pending_outbox = await repository.outbox_pending_count()
        return {
            "status": "ok",
            "observability": {"state": observability.state, "model_configured": language_model is not None,
                              "errors": observability.errors},
            "remediation": {"case_id": app.state.active_remediation_case_id,
                            "error": app.state.remediation_error},
            "storage": "postgresql" if isinstance(repository, PostgresEventRepository) else "memory",
            "event_bus": "kafka" if isinstance(bus, KafkaRedpandaEventBus) else "memory",
            "simulator_queue_depth": simulator_adapter._queue.qsize(),
            "simulator_adapter_failures": simulator_adapter.failures,
            "ghost_test_summary": {
                "source": summary_provider.source if summary_provider is not None else "local",
                "model": summary_provider.model if summary_provider is not None else None,
                "fallbacks": ghost_test_agent.summary_failures,
            },
            "metrics": dict(service.metrics),
            "outbox_pending": pending_outbox,
        }

    async def observe_status_snapshot() -> dict[str, object]:
        health = await health_snapshot()
        if isinstance(bus, KafkaRedpandaEventBus):
            kafka = {
                "configured": True,
                "provider": "aiven" if "aivencloud.com" in bus.bootstrap_servers else "kafka",
                "state": "connected" if bus._producer is not None else "disconnected",
                "security": bus.security_protocol,
                "topic": bus.topic,
                "published": bus.published_count,
                "dead_lettered": bus.dead_letter_count,
            }
        else:
            kafka = {
                "configured": False,
                "provider": "memory",
                "state": "ready",
                "security": "local",
                "topic": None,
                "published": getattr(bus, "published_count", 0),
                "dead_lettered": len(getattr(bus, "dead_letters", [])),
            }
        return {"health": health, "kafka": kafka}

    app.include_router(build_approval_router(
        remediation_runtime.remediation.approvals,
        after_decision=after_approval_decision,
    ))
    app.include_router(build_observe_router(operational_logs, observe_status_snapshot))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return await health_snapshot()

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
        if body.type in {ContractObjectType.EVENT, ContractObjectType.COMPLAINT}:
            schedule_investigation()
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

    @app.get("/observability")
    async def observability_status():
        return observability.snapshot()

    @app.post("/observability/investigate")
    async def observability_investigate():
        return await process_observations()

    @app.post("/observability/replay")
    async def observability_replay(until: datetime):
        if until.tzinfo is None:
            raise HTTPException(status_code=422, detail="until requires an explicit UTC offset")
        path = Path(__file__).resolve().parents[1] / "fixtures" / "full_incident_stream.json"
        await stop_investigation()
        async with observability._lock:
            observability.reset()
            for item in replay_observations(path, until):
                observability.observe("event" if hasattr(item, "service") else "complaint", item.model_dump(mode="json"))
        result = await observability.run(until=until)
        handoff = remediation_runtime.make_handoff(observability)
        if handoff is not None:
            case = remediation_runtime.remediation.on_validated(handoff)
            app.state.active_remediation_case_id = case.incident_id
        await remediation_runtime.flush_events(service)
        await persist_remediation_state()
        return result

    @app.post("/ghost-test/rerun", response_model=RerunReceipt, status_code=202)
    async def ghost_test_rerun(body: RerunRequest) -> RerunReceipt:
        try:
            return await ghost_test_agent.rerun(body)
        except (UnknownHypothesisError, RerunNotReadyError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"No previous Ghost Test run is available for hypothesis {exc.args[0]!r}",
            ) from exc

    @app.post("/twin/simulate", response_model=TwinSimulationReceipt)
    async def twin_simulate(body: TwinSimulationRequest) -> TwinSimulationReceipt:
        return control_plane.twin.simulate(body)

    @app.get("/remediation/{incident_id}")
    async def remediation_case(incident_id: str):
        case = remediation_runtime.remediation.store.get(incident_id)
        if case is None:
            raise HTTPException(status_code=404, detail="remediation case not found")
        return {
            "incident_id": case.incident_id,
            "status": str(case.status),
            "plan": case.plan.model_dump(mode="json"),
            "blast_radius": case.blast_radius.model_dump(mode="json"),
            "approval_ids": case.approval_ids,
            "events": [event.model_dump(mode="json") for event in case.events],
        }

    @app.post("/demo/start")
    async def demo_start() -> dict[str, object]:
        await stop_investigation()
        async with observability._lock:
            observability.reset()
            app.state.active_remediation_case_id = None
            app.state.remediation_error = None
            remediation_runtime.remediation.approvals.reset_demo_state()
            remediation_runtime.remediation.store.clear()
            remediation_runtime.bank_data.clear()
            remediation_runtime.world.bank.routes["BANK_X"] = "gateway_b.bank_x"
            remediation_runtime.world.bank.applied.clear()
            await remediation_runtime.flush_events(service)
            await persist_remediation_state()
            await ghost_test_agent.reset()
            return await control_plane.start_demo()

    @app.post("/demo/inject")
    async def demo_inject(body: DemoInjectRequest | None = None) -> dict[str, object]:
        try:
            receipt = await control_plane.inject_demo(body or DemoInjectRequest())
            schedule_investigation()
            return receipt
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/demo/reset")
    async def demo_reset() -> dict[str, object]:
        await stop_investigation()
        async with observability._lock:
            receipt = await control_plane.reset_demo()
            observability.reset()
            app.state.active_remediation_case_id = None
            app.state.remediation_error = None
            remediation_runtime.remediation.approvals.reset_demo_state()
            remediation_runtime.remediation.store.clear()
            remediation_runtime.bank_data.clear()
            remediation_runtime.world.bank.routes["BANK_X"] = "gateway_b.bank_x"
            remediation_runtime.world.bank.applied.clear()
            await remediation_runtime.flush_events(service)
            await persist_remediation_state()
            await ghost_test_agent.reset()
            return receipt

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
