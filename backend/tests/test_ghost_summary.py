from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient

from avera.api.app import create_app
from avera.llm import OpenAITestSummaryProvider


HYPOTHESIS = {
    "id": "H1",
    "claim": "A user-supplied claim that must not be sent to the model.",
    "zone": "gateway_b.bank_x_connector",
    "evidence_for": ["synthetic acknowledgement timeouts"],
    "evidence_against": [],
    "disproof": "A healthy control route completes the transfer.",
    "routed_to": ["payment-router"],
    "prior_rank": 1,
}


class FakeSummaryProvider:
    source = "test-double"
    model = "offline-test-model"

    def __init__(self) -> None:
        self.evidence = None

    async def summarize(self, evidence: list[dict[str, object]]) -> str:
        self.evidence = evidence
        return "The simulator reproduced the gateway symptom in its test runs."


class FailingSummaryProvider:
    source = "test-double"
    model = "offline-test-model"

    async def summarize(self, evidence: list[dict[str, object]]) -> str:
        raise TimeoutError("simulated provider timeout")


def test_model_summary_is_advisory_and_receives_only_aggregate_measurements(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    provider = FakeSummaryProvider()
    payload = {"hypotheses": [HYPOTHESIS], "runs": 5}

    with TestClient(create_app()) as local_client:
        local = local_client.post("/hypotheses", json=payload).json()
    with TestClient(create_app(test_summary_provider=provider)) as model_client:
        modeled_response = model_client.post("/hypotheses", json=payload)

    assert modeled_response.status_code == 202, modeled_response.text
    modeled = modeled_response.json()
    assert modeled["summary"] == "The simulator reproduced the gateway symptom in its test runs."
    assert modeled["summary_source"] == "test-double"
    assert modeled["summary_model"] == "offline-test-model"
    assert modeled["evaluations"][0]["test_result"]["confidence"] == local["evaluations"][0]["test_result"]["confidence"]
    assert modeled["evaluations"][0]["test_result"]["verdict"] == local["evaluations"][0]["test_result"]["verdict"]
    assert provider.evidence is not None
    assert "claim" not in provider.evidence[0]
    assert "customer_token" not in provider.evidence[0]
    assert "evidence_for" not in provider.evidence[0]


def test_model_failure_falls_back_to_local_summary(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    app = create_app(test_summary_provider=FailingSummaryProvider())
    with TestClient(app) as client:
        response = client.post("/hypotheses", json={"hypotheses": [HYPOTHESIS], "runs": 5})

    assert response.status_code == 202, response.text
    result = response.json()
    assert result["summary_source"] == "local"
    assert "Deterministic simulator checks" in result["summary"]
    assert app.state.ghost_test_agent.summary_failures == 1


def test_openai_adapter_uses_responses_without_tools_or_raw_customer_data(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class FakeResponses:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(output_text="Aggregate simulator summary.")

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, timeout: float, max_retries: int) -> None:
            assert api_key == "test-api-key"
            assert timeout == 8.0
            assert max_retries == 1
            self.responses = FakeResponses()

        async def close(self) -> None:
            return None

    fake_openai = ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    provider = OpenAITestSummaryProvider("gpt-5.4-mini", api_key="test-api-key")
    evidence = [{"test_type": "gateway_ack", "runs": 5, "reproduced": 5, "confidence": 0.96}]
    summary = asyncio.run(provider.summarize(evidence))
    asyncio.run(provider.close())

    assert summary == "Aggregate simulator summary."
    assert recorded["model"] == "gpt-5.4-mini"
    assert recorded["store"] is False
    assert "tools" not in recorded
    assert '"customer_token"' not in recorded["input"]
