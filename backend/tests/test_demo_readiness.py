"""Ten clean end-to-end repetitions of Koded's seeded demo path."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from avera.api.app import create_app


RUNS = 10
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "avera" / "fixtures" / "full_incident_stream.json"


def _fixture_hypotheses() -> list[dict[str, object]]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [record["data"] for record in fixture["records"] if record["type"] == "hypothesis"]


def test_seeded_incident_completes_ten_clean_cycles(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    hypotheses = _fixture_hypotheses()
    assert {item["id"] for item in hypotheses} >= {"H1", "H3"}

    with TestClient(create_app()) as client:
        for cycle in range(1, RUNS + 1):
            started = client.post("/demo/start")
            assert started.status_code == 200, f"cycle {cycle}: {started.text}"
            assert started.json()["bank_x_route"] == "gateway_b"

            injected = client.post("/demo/inject")
            assert injected.status_code == 200, f"cycle {cycle}: {injected.text}"
            incident = injected.json()
            assert incident["transactions"] == 1284
            assert incident["customers"] == 1109
            assert incident["total_simulated_event_count"] > 5000
            assert client.post("/demo/inject").json() == incident

            twin = client.post(
                "/twin/simulate",
                json={"destination_bank": "BANK_X", "to_gateway": "gateway_c", "runs": 5},
            )
            assert twin.status_code == 200, f"cycle {cycle}: {twin.text}"
            assert twin.json()["baseline_failures"] == 5
            assert twin.json()["candidate_failures"] == 0
            assert client.get("/service-map").json()["routes"]["BANK_X"] == "gateway_b"

            evaluated = client.post("/hypotheses", json={"hypotheses": hypotheses, "runs": 5})
            assert evaluated.status_code == 202, f"cycle {cycle}: {evaluated.text}"
            scores = {
                item["hypothesis"]["id"]: item["test_result"]["confidence"]
                for item in evaluated.json()["evaluations"]
            }
            assert scores["H1"] >= 0.85, f"cycle {cycle}: H1={scores['H1']}"
            assert scores["H3"] < 0.25, f"cycle {cycle}: H3={scores['H3']}"

            approval = client.post(
                "/approvals",
                json={
                    "level": "level_3",
                    "kind": "config",
                    "payload": {
                        "action": "reroute",
                        "destination_bank": "BANK_X",
                        "to_gateway": "gateway_c",
                    },
                },
            )
            assert approval.status_code == 201, f"cycle {cycle}: {approval.text}"
            approval_id = approval.json()["id"]
            assert client.post("/admin/route", json={"approval_id": approval_id}).status_code == 409
            decision = client.post(
                f"/approvals/{approval_id}",
                json={"decision": "approved", "approver": "demo-operator"},
            )
            assert decision.status_code == 200, f"cycle {cycle}: {decision.text}"
            route = client.post("/admin/route", json={"approval_id": approval_id})
            assert route.status_code == 200, f"cycle {cycle}: {route.text}"
            assert client.get("/service-map").json()["routes"]["BANK_X"] == "gateway_c"

            rerun = client.post("/ghost-test/rerun", json={"hypothesis_id": "H1", "runs": 5})
            assert rerun.status_code == 202, f"cycle {cycle}: {rerun.text}"
            assert rerun.json()["test_result"]["rerun_passed"] is True

            reset = client.post("/demo/reset")
            assert reset.status_code == 200, f"cycle {cycle}: {reset.text}"
            assert reset.json()["bank_x_route"] == "gateway_b"
            assert client.get("/approvals").json() == []

        health = client.get("/health").json()
        assert health["simulator_queue_depth"] == 0
        assert health["simulator_adapter_failures"] == 0
        assert health["metrics"]["duplicates"] == 0
