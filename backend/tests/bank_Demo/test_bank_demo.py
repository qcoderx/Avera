from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from avera.api.app import create_app
from avera.bank_Demo.service import BankDemoRuntime
from avera.bank_Demo.workspace import BUGGY_SOURCE, RepairWorkspace, UnsafeRepair


@pytest.mark.asyncio
async def test_duplicate_debit_is_reproduced_then_repaired_in_real_source(tmp_path) -> None:
    workspace = RepairWorkspace(tmp_path)
    published = []

    async def publish(object_type, payload) -> None:
        published.append((object_type, payload))

    runtime = BankDemoRuntime(workspace=workspace, publish_event=publish)
    runtime.step_delay = 0

    assert workspace.run_tests().passed is False
    triggered = await runtime.transfer()
    assert triggered["status"] == "awaiting_report"
    assert triggered["transfer"]["attempts"] == 2
    assert triggered["transfer"]["debits"] == 2
    assert triggered["account"]["balance_ngn"] == 435_300

    await runtime.report_issue("I was debited twice after my transfer timed out.")
    resolved = await runtime.wait_for_repair()

    assert resolved["status"] == "resolved"
    assert resolved["transfer"]["debits"] == 2
    assert resolved["account"]["balance_ngn"] == 460_300
    assert resolved["ledger"][0]["title"] == "Duplicate debit reversal"
    assert resolved["ledger"][0]["direction"] == "credit"
    assert resolved["ledger"][0]["amount_ngn"] == 25_000
    assert resolved["report"]["tests_passed"] == 3
    assert resolved["report"]["verification"] == "1 transfer intent · 2 delivery attempts · 1 debit"
    assert resolved["report"]["files_changed"] == ["bank_Demo/workspace/transfer_service.py"]
    assert workspace.read_source() != BUGGY_SOURCE
    assert workspace.run_tests().passed is True

    published_types = [object_type for object_type, _ in published]
    assert published_types[:5] == ["event"] * 5
    assert "complaint" in published_types
    assert "incident" in published_types
    assert "hypothesis" in published_types
    assert "test_result" in published_types
    assert "blast_radius" in published_types
    assert "fix_plan" in published_types
    resolution_actions = [
        payload["action"]
        for object_type, payload in published
        if object_type == "resolution_event"
    ]
    assert resolution_actions == [
        "reproduce_bank_transfer_failure",
        "apply_bank_transfer_patch",
        "run_bank_regression_tests",
        "verify_bank_transfer_journey",
        "reverse_duplicate_debit",
        "mark_incident_resolved",
    ]

    next_transfer = await runtime.transfer()
    assert next_transfer["transfer"]["debits"] == 1
    assert next_transfer["account"]["balance_ngn"] == 435_300


def test_repair_workspace_rejects_source_with_external_capabilities(tmp_path) -> None:
    workspace = RepairWorkspace(tmp_path)
    malicious = """import os

def build_idempotency_key(transfer_intent_id: str, attempt: int) -> str:
    os.remove('/tmp/example')
    return transfer_intent_id
"""
    with pytest.raises(UnsafeRepair):
        workspace.write_source(malicious)


def test_bank_demo_api_triggers_and_completes_the_full_flow(monkeypatch) -> None:
    monkeypatch.setenv("AVERA_BANK_DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("AVERA_OBSERVABILITY_MODEL", raising=False)
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)

    with TestClient(create_app()) as client:
        initial = client.get("/bank-demo/state")
        assert initial.status_code == 200
        assert initial.json()["status"] == "idle"

        transfer = client.post("/bank-demo/transfer", json={"amount_ngn": 25_000})
        assert transfer.status_code == 200
        assert transfer.json()["status"] == "awaiting_report"
        assert transfer.json()["transfer"]["debits"] == 2

        report = client.post(
            "/bank-demo/report",
            json={"complaint": "My transfer timed out and I can see two debits."},
        )
        assert report.status_code == 200
        assert report.json()["status"] == "reported"

        state = report.json()
        for _ in range(100):
            state = client.get("/bank-demo/state").json()
            if state["status"] in {"awaiting_approval", "failed"}:
                break
            time.sleep(0.01)

        assert state["status"] == "awaiting_approval"
        assert state["account"]["balance_ngn"] == 435_300
        assert state["report"] is None

        approvals = client.get("/approvals", params={"incident_id": "INC-BANK-001"}).json()
        pending = next(item for item in approvals if item["status"] == "pending")
        assert pending["kind"] == "code"
        assert pending["payload"]["target"] == "bank_Demo/workspace/transfer_service.py"
        assert pending["payload"]["sandbox"]["tests_run"] == 3
        assert pending["payload"]["diff"]

        decision = client.post(
            f"/approvals/{pending['id']}",
            json={"decision": "approve", "payload_hash": pending["payload_hash"]},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"

        for _ in range(100):
            state = client.get("/bank-demo/state").json()
            if state["status"] in {"resolved", "failed"}:
                break
            time.sleep(0.01)

        assert state["status"] == "resolved"
        assert state["account"]["balance_ngn"] == 460_300
        assert state["report"]["tests_passed"] == 3
        assert all(step["status"] == "complete" for step in state["steps"])

        stream_records = client.app.state.event_repository._records
        assert any(
            record.type.value == "incident"
            and record.data["id"] == "INC-BANK-001"
            and record.data["status"] == "resolved"
            for record in stream_records
        )
        assert any(
            record.type.value == "resolution_event"
            and record.data["action"] == "verify_bank_transfer_journey"
            for record in stream_records
        )
        assert any(
            record.type.value == "resolution_event"
            and record.data["action"] == "reverse_duplicate_debit"
            and record.data["detail"]["amount_ngn"] == 25_000
            for record in stream_records
        )

        reset = client.post("/bank-demo/reset")
        assert reset.status_code == 200
        assert reset.json()["status"] == "idle"
