"""FastAPI application for typed ingestion, service-map reads, and SSE replay."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
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
from avera.api.auth import current_approver
from avera.remediation.approvals.api import build_router as build_approval_router
from avera.remediation.approvals import Approver
from avera.remediation.integration import RemediationRuntime, build_runtime
from avera.remediation.models import Role

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


class DevAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class DevAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: Role


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
    test_summary_provider: GhostSummaryProvider | None = None,
    observability_model=None,
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
        await repository.start()
        try:
            await bus.start()
            await simulator_adapter.start()
            app.state.outbox_task = asyncio.create_task(dispatch_outbox(), name="avera-event-outbox")
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
            await simulator_adapter.close()
            if language_model is not None and hasattr(language_model, "close"):
                await language_model.close()
            if summary_provider is not None and hasattr(summary_provider, "close"):
                await summary_provider.close()
            await bus.close()
            await repository.close()

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
    app.state.event_service = service
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

    investigation_requested = False

    async def process_observations():
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
    app.include_router(build_approval_router(
        remediation_runtime.remediation.approvals,
        current_approver=current_approver,
        after_decision=lambda: remediation_runtime.flush_events(service),
    ))

    @app.get("/health")
    async def health() -> dict[str, Any]:
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

    @app.post("/dev/auth/token", response_model=DevAuthTokenResponse)
    async def issue_local_dev_token(
        body: DevAuthRequest,
        request: Request,
        response: Response,
    ) -> DevAuthTokenResponse:
        """Issue a short-lived approver token for loopback-only local UI development."""
        enabled = os.getenv("AVERA_DEV_AUTH_ENABLED", "").strip().casefold() in {"1", "true", "yes"}
        environment = os.getenv("AVERA_ENVIRONMENT", "production").strip().casefold()
        if not enabled or environment != "development":
            raise HTTPException(status_code=404, detail="not found")

        remote_host = request.client.host if request.client else ""
        try:
            if not ip_address(remote_host).is_loopback:
                raise HTTPException(status_code=404, detail="not found")
        except ValueError:
            raise HTTPException(status_code=404, detail="not found") from None

        secret = os.getenv("AVERA_APPROVER_JWT_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise HTTPException(status_code=503, detail="JWT verification is not configured")

        now = int(time.time())
        issuer = os.getenv("AVERA_APPROVER_JWT_ISSUER", "avera")
        audience = os.getenv("AVERA_APPROVER_JWT_AUDIENCE", "avera-api")
        subject = f"local-dev-{body.role.value}"
        token = jwt.encode(
            {"sub": subject, "role": body.role.value, "iss": issuer, "aud": audience,
             "iat": now, "exp": now + 600},
            secret,
            algorithm="HS256",
        )
        response.headers["Cache-Control"] = "no-store"
        return DevAuthTokenResponse(access_token=token, expires_in=600, role=body.role)

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
    async def remediation_case(incident_id: str, _: Approver = Depends(current_approver)):
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
