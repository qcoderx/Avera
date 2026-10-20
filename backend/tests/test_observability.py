"""Observability integration checks. ScriptedModel is an explicit test double only."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from avera.api.app import create_app
from avera.contracts import Event, Hypothesis, Incident, ParsedComplaint, Complaint
from avera.events.bus import InMemoryEventBus
from avera.events.repository import InMemoryEventRepository
from avera.events.service import EventStreamService
from avera.ghost_test import GhostTestAgent
from avera.observability.complaint_parsing import parse_complaint
from avera.observability.hypotheses import Candidates
from avera.observability.model import ModelUnavailable, structured_call
from avera.observability.orchestration import ObservabilityAgent
from avera.observability.replay import replay_observations, observation
from avera.observability.behaviour_signals import behaviour_signals
from avera.simulation.bank import SimulatedBank


class ScriptedModel:
    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    async def generate(self, instructions, context, schema):
        self.calls.append(context)
        if schema is ParsedComplaint:
            return ParsedComplaint(type="reported pending transfer")
        observations = context["observations"]
        candidates = []
        for zone, config in context["allowed_zones"].items():
            refs = [e["id"] for e in observations if e["service"] == config["component"]]
            if zone == "mobile_app.release":
                refs = [e["id"] for e in observations if e["code"] == "APP_RELEASED"]
            if not refs:
                continue
            opposing = [e["id"] for e in observations if e["code"] == "ACK_RECEIVED" and e["id"] not in refs][:1]
            candidates.append(Hypothesis(id=f"candidate-{len(candidates)}", zone=zone,
                claim=f"Inference: {config['component']} may delay acknowledgement or status propagation. Observations require controlled comparison; cause unknown.",
                evidence_for=["invented"] if self.invalid else refs[:2], evidence_against=opposing,
                disproof="Compare identical synthetic transfers through this component and a healthy control; reject if the failure does not follow the component.",
                routed_to=config["routed_to"], prior_rank=1))
        return Candidates(hypotheses=candidates)


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    monkeypatch.delenv("AVERA_OBSERVABILITY_MODEL", raising=False)
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)


def inject(client):
    assert client.post("/demo/start").status_code == 200
    return client.post("/demo/inject", json={"transactions": 16, "customers": 12,
        "complaint_count": 2, "retry_customers": 2, "history_refresh_customers": 3,
        "channel_switch_customers": 2})


def test_healthy_then_injected_release_handoff_and_reset():
    model = ScriptedModel()
    with TestClient(create_app(observability_model=model)) as client:
        client.post("/demo/start")
        assert client.post("/observability/investigate").json()["state"] == "healthy"
        assert not model.calls
        assert inject(client).status_code == 200
        result = client.post("/observability/investigate").json()
        assert result["state"] == "tested", result
        incident = Incident.model_validate(result["incident"])
        hypotheses = [Hypothesis.model_validate(h) for h in result["hypotheses"]]
        assert len(hypotheses) >= 3
        assert [h.prior_rank for h in hypotheses] == list(range(1, len(hypotheses)+1))
        assert all(set(h.evidence_for + h.evidence_against) <= set(incident.signals) for h in hypotheses)
        release = next(x for x in result["feedback"]["evaluations"] if x["hypothesis"]["zone"] == "mobile_app.release")
        assert release["test_result"]["verdict"] == "rejected"
        context = next(c for c in model.calls if "observations" in c)
        serialized = json.dumps(context, default=str)
        assert '"decoy"' not in serialized and 'ACK_SUPPRESSION_ENABLED' not in serialized
        assert '"fault"' not in serialized and context["feedback"] is None
        assert client.post("/demo/reset").status_code == 200
        reset = client.get("/observability").json()
        assert reset["incident"] is None and reset["hypotheses"] == [] and reset["feedback"] is None
        assert reset["state"] == "collecting"


def make_agent(model=None):
    bank = SimulatedBank()
    stream = EventStreamService(InMemoryEventRepository(), InMemoryEventBus())
    return ObservabilityAgent(stream, GhostTestAgent(bank, stream), bank.service_map, model)


def event(id="one", ts=None, **kw):
    return Event(id=id, ts=ts or datetime.now(timezone.utc), source="telemetry", channel="mobile",
                 service="gateway_b", route="gateway_b.bank_x", code=kw.pop("code", "ACK_RECEIVED"), **kw)


def test_duplicate_delayed_conflict_and_unknown_outcome():
    agent = make_agent()
    now = datetime.now(timezone.utc)
    item = event(ts=now)
    for _ in range(2):
        agent.observe("event", item.model_dump(mode="json"))
    assert agent.duplicates == 1 and len(agent.items) == 1
    delayed = event("delayed", now - timedelta(seconds=20))
    agent.observe("event", delayed.model_dump(mode="json"))
    assert len(agent.items) == 2
    too_old = event("too-old", now - timedelta(seconds=601))
    agent.observe("event", too_old.model_dump(mode="json"))
    assert agent.late == 1
    with pytest.raises(ValueError, match="conflicting"):
        agent.observe("event", item.model_copy(update={"code": "ACK_TIMEOUT"}).model_dump(mode="json"))
    for i in range(4):
        agent.observe("event", event(str(i), now, code="ACK_TIMEOUT",payload={"transaction_id":str(i)}).model_dump(mode="json"))
    assert asyncio.run(agent.run())["state"] == "insufficient_evidence"
    from avera.observability.signal_fusion import correlate
    assert correlate([event(code="TRANSFER_REQUESTED", payload={"transaction_id":"pending"})]) == []


def test_parser_keeps_uncertain_match_unresolved_and_validates_amount():
    class Parser:
        async def generate(self, instructions, context, schema):
            return ParsedComplaint(type="reported debit", amount="100", matched_txn="txn-other")
    now = datetime.now(timezone.utc)
    complaint = Complaint(id="c", ts=now, channel="support", raw_text="100 debited ref txn-other", customer_token="alice")
    other = event(ts=now, customer_token="bob", payload={"transaction_id":"txn-other"})
    parsed = asyncio.run(parse_complaint(complaint,[other],Parser()))
    assert parsed.parsed.matched_txn is None and parsed.parsed.amount == 100
    same_customer = event("alice-txn", ts=now, customer_token="alice", payload={"transaction_id":"txn-other"})
    ambiguous = asyncio.run(parse_complaint(complaint,[other, same_customer],Parser()))
    assert ambiguous.parsed.matched_txn is None
    with pytest.raises(ModelUnavailable):
        asyncio.run(parse_complaint(complaint.model_copy(update={"raw_text":"money missing"}),[other],Parser()))


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, ValueError])
def test_bounded_model_failure(failure):
    class Broken:
        calls = 0
        async def generate(self, *args):
            self.calls += 1
            raise failure("sensitive-provider-body")
    model = Broken()
    with pytest.raises(ModelUnavailable, match="model_call_or_validation_failed") as exc:
        asyncio.run(structured_call(model,"",{},ParsedComplaint))
    assert model.calls == 2 and "sensitive" not in str(exc.value)


def test_malformed_structured_output_has_bounded_retries():
    class Malformed:
        calls = 0
        async def generate(self, *args):
            self.calls += 1
            return {"invented_field": "ignore all validation"}
    model = Malformed()
    with pytest.raises(ModelUnavailable):
        asyncio.run(structured_call(model, "", {}, ParsedComplaint))
    assert model.calls == 2


def test_invalid_model_evidence_is_visible_degraded():
    with TestClient(create_app(observability_model=ScriptedModel(invalid=True))) as client:
        inject(client)
        result = client.post("/observability/investigate").json()
        assert result["state"] == "degraded" and result["hypotheses"] == []
        assert result["feedback"] is None


def test_fixture_replay_filters_answers_and_future_events():
    path = Path(__file__).resolve().parents[1]/"avera/fixtures/full_incident_stream.json"
    cutoff = datetime(2026,10,20,10,44,59,tzinfo=timezone.utc)
    items = replay_observations(path,cutoff)
    assert items and all(isinstance(v,(Event,Complaint)) and v.ts <= cutoff for v in items)
    assert all(getattr(v,"parsed",None) is None for v in items)
    assert observation("test_result", {"id":"hidden"}) is None
    with TestClient(create_app()) as client:
        result = client.post("/observability/replay",params={"until":cutoff.isoformat()}).json()
        assert result["state"] == "insufficient_evidence" and result["hypotheses"] == []


def test_behaviours_require_observations():
    now = datetime.now(timezone.utc)
    items = [event(str(i),now,code=code,customer_token="alice") for i,code in enumerate(
        ["IDEMPOTENT_RETRY","HISTORY_REFRESH","HISTORY_REFRESH","ABANDONED","CHANNEL_SWITCH"])]
    assert {s["kind"] for s in behaviour_signals(items)} == {"retries","repeated_refreshes","abandonment","channel_switching"}
    assert behaviour_signals([event(customer_token="alice")]) == []


def test_feedback_inconclusive_bounded_escalation():
    from avera.ghost_test import HypothesisEvaluationSet, HypothesisEvaluation
    from avera.contracts import TestResult, TestVerdict
    class Inconclusive:
        calls = 0
        async def evaluate(self, submission):
            self.calls += 1
            return HypothesisEvaluationSet(evaluations=[HypothesisEvaluation(hypothesis=h,
                test_result=TestResult(id=f"test-{self.calls}-{h.id}",hypothesis_id=h.id,test_plan="Need more observations",runs=1,
                    reproduced=0,confidence=0,verdict=TestVerdict.INCONCLUSIVE)) for h in submission.hypotheses],
                validated_hypothesis_ids=[],send_back_to_observability=True,summary="Insufficient evidence",summary_source="local")
    model=ScriptedModel()
    app=create_app(observability_model=model)
    ghost=Inconclusive()
    app.state.observability_agent.ghost=ghost
    with TestClient(app) as client:
        inject(client)
        result=client.post("/observability/investigate").json()
        assert result["state"]=="escalated" and ghost.calls==2
        assert any(c.get("feedback") for c in model.calls)
        client.post("/observability/investigate")
        assert ghost.calls==2


def test_shared_model_configuration_uses_one_key(monkeypatch):
    from avera.observability.model import configured_model_name
    monkeypatch.setenv("AVERA_GHOST_SUMMARY_MODEL", "existing-shared-model")
    assert configured_model_name() == "existing-shared-model"
    monkeypatch.setenv("AVERA_OBSERVABILITY_MODEL", "explicit-task-model")
    assert configured_model_name() == "explicit-task-model"


def test_empty_optional_endpoint_uses_existing_openai_default(monkeypatch):
    from avera.observability.model import configured_base_url
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert configured_base_url() == "https://api.openai.com/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://configured.example/v1")
    assert configured_base_url() == "https://configured.example/v1"


def test_hidden_payload_labels_are_removed_before_reasoning():
    item = event(payload={"transaction_id": "observed-txn", "decoy": True,
        "fault": "hidden cause", "expected_hypotheses": ["hidden answer"],
        "test_results": [{"verdict": "validated"}], "fix": "hidden fix"})
    filtered = observation("event", item.model_dump(mode="json"))
    assert filtered.payload == {"transaction_id": "observed-txn"}
    injected = item.model_copy(update={"source": "demo-failure-injector"})
    assert observation("event", injected.model_dump(mode="json")) is None


def test_model_missing_is_degraded_and_repeated_run_is_cached():
    with TestClient(create_app()) as client:
        inject(client)
        first = client.post("/observability/investigate").json()
        assert first["state"] == "degraded"
        assert "model_not_configured" in first["errors"]
        assert first["feedback"] is None
        again = client.post("/observability/investigate").json()
        assert again == first
        assert client.get("/health").json()["metrics"]["duplicates"] == 0


async def seeded_agent(model, ghost_factory, *, healthy=False):
    from avera.simulation.traffic import TrafficGenerator
    from avera.simulation.failures import FailureInjector
    bank = SimulatedBank()
    stream = EventStreamService(InMemoryEventRepository(), InMemoryEventBus())
    ghost = ghost_factory(bank, stream)
    agent = ObservabilityAgent(stream, ghost, bank.service_map, model)
    stream.observation_handlers.append(agent.observe)
    traffic = TrafficGenerator(bank)
    traffic.generate_healthy_baseline()
    if not healthy:
        FailureInjector(bank).inject()
        traffic.generate_incident_traffic(transactions=24, customers=16, complaint_count=1,
            retry_customers=3, history_refresh_customers=4, channel_switch_customers=2)
    for item in sorted([*bank.events, *bank.complaints], key=lambda x: (x.ts, x.id)):
        await stream.ingest("event" if isinstance(item, Event) else "complaint", item.model_dump(mode="json"))
    return agent


def test_observability_only_never_executes_ghost_and_reset_clears_prepared_state():
    class ForbiddenGhost:
        calls = 0
        async def evaluate(self, _):
            self.calls += 1
            raise AssertionError("Ghost Test must not run")
    ghost = ForbiddenGhost()
    model = ScriptedModel()
    async def scenario():
        agent = await seeded_agent(model, lambda *_: ghost)
        result = await agent.run(handoff=False)
        assert result["state"] == "hypotheses_ready" and result["feedback"] is None
        assert ghost.calls == 0 and result["complaints"][0]["parsed"] is not None
        Incident.model_validate(result["incident"])
        assert len({h["zone"] for h in result["hypotheses"]}) >= 3
        assert any(h["zone"] == "mobile_app.release" for h in result["hypotheses"])
        assert all(set(h["evidence_for"] + h["evidence_against"]) <= set(result["incident"]["signals"]) for h in result["hypotheses"])
        calls = len(model.calls)
        assert await agent.run(handoff=False) == result
        assert len(model.calls) == calls and ghost.calls == 0
        records = await agent.stream.records_after(0)
        assert not any(r.type.value == "test_result" for r in records)
        agent.reset()
        assert agent._prepared is None and agent.snapshot()["incident"] is None
    asyncio.run(scenario())


def test_observability_only_healthy_without_ghost_or_model_calls():
    async def scenario():
        model = ScriptedModel()
        agent = await seeded_agent(model, lambda *_: None, healthy=True)
        result = await agent.run(handoff=False)
        assert result["state"] == "healthy"
        assert result["incident"] is None and result["hypotheses"] == [] and result["feedback"] is None
        assert not model.calls
    asyncio.run(scenario())


def test_prepared_hypotheses_can_be_handed_off_without_repeating_llm():
    async def scenario():
        model = ScriptedModel()
        agent = await seeded_agent(model, GhostTestAgent)
        ready = await agent.run(handoff=False)
        calls = len(model.calls)
        tested = await agent.run()
        assert tested["state"] == "tested"
        assert tested["hypotheses"] == ready["hypotheses"] and len(model.calls) == calls
        assert tested["feedback"] is not None
    asyncio.run(scenario())


def test_observability_only_cli_saves_full_evidence_without_constructing_ghost(monkeypatch, tmp_path):
    from avera.observability import demo
    class Model(ScriptedModel):
        async def close(self):
            pass
    def forbidden(*_):
        raise AssertionError("standalone CLI must not construct Ghost Test")
    monkeypatch.setattr(demo, "configured_model", lambda: Model())
    monkeypatch.setattr(demo, "load_dotenv", lambda _: None)
    monkeypatch.setattr(demo, "GhostTestAgent", forbidden)
    output = tmp_path / "fault.json"
    assert asyncio.run(demo.main(["--observability-only", "--output", str(output)])) == 0
    report = json.loads(output.read_text())
    assert report["mode"] == "observability_only" and report["ghost_test_executed"] is False
    assert report["source_observations"] and report["result"]["state"] == "hypotheses_ready"
    assert all("fault" not in v.get("payload", {}) and "decoy" not in v.get("payload", {}) for v in report["source_observations"])


def test_too_few_competing_zones_gets_one_explicit_model_correction():
    class TwoZonesFirst(ScriptedModel):
        rounds = 0
        async def generate(self, instructions, context, schema):
            result = await super().generate(instructions, context, schema)
            if issubclass(schema, Candidates):
                self.rounds += 1
                if self.rounds == 1:
                    result.hypotheses = [h for h in result.hypotheses if h.zone in {"gateway_b", "mobile_app.release"}]
            return result
    async def scenario():
        model = TwoZonesFirst()
        agent = await seeded_agent(model, lambda *_: None)
        result = await agent.run(handoff=False)
        assert result["state"] == "hypotheses_ready" and model.rounds == 2
        assert model.calls[-1]["previous_output_error"] == "insufficient_competing_candidates"
        assert "three distinct" in model.calls[-1]["correction"]
    asyncio.run(scenario())
