"""The seeded bank demo remains repeatable after the approval API moved to JWT."""

from __future__ import annotations

from fastapi.testclient import TestClient

from avera.api.app import create_app


def test_demo_cycles_keep_route_mutations_behind_platform_approvals(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("AVERA_OBSERVABILITY_MODEL", raising=False)

    with TestClient(create_app()) as client:
        for _ in range(3):
            started = client.post("/demo/start")
            assert started.status_code == 200
            assert started.json()["bank_x_route"] == "gateway_b"

            incident = client.post("/demo/inject").json()
            assert incident["transactions"] == 1284
            assert incident["customers"] == 1109
            assert incident["total_simulated_event_count"] > 5000

            twin = client.post("/twin/simulate", json={
                "destination_bank": "BANK_X", "to_gateway": "gateway_c", "runs": 5,
            })
            assert twin.status_code == 200
            assert twin.json()["baseline_failures"] == 5
            assert twin.json()["candidate_failures"] == 0
            assert client.get("/service-map").json()["routes"]["BANK_X"] == "gateway_b"

            # The old body-asserted approver and unguarded route mutation are gone.
            assert client.get("/approvals").status_code == 401
            assert client.post("/approvals", json={}).status_code == 405
            assert client.post("/admin/route", json={"approval_id": "anything"}).status_code == 404

            reset = client.post("/demo/reset")
            assert reset.status_code == 200
            assert reset.json()["bank_x_route"] == "gateway_b"

        health = client.get("/health").json()
        assert health["simulator_queue_depth"] == 0
        assert health["simulator_adapter_failures"] == 0
        assert health["metrics"]["duplicates"] == 0
