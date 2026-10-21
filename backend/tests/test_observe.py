"""The public observe console exposes useful operations data without secrets."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from avera.api.app import create_app
from avera.api.observe import OperationalLogStore, sanitize_log_message


def test_log_redaction_removes_common_secrets_and_customer_phone_numbers() -> None:
    message = sanitize_log_message(
        "Bearer abc.def.ghi password=hunter2 "
        "postgresql://avera:database-secret@db.example/app "
        "sk-proj-thisIsAnApiKey123456 customer=+2348012345678"
    )

    assert "abc.def" not in message
    assert "hunter2" not in message
    assert "database-secret" not in message
    assert "thisIsAnApiKey" not in message
    assert "2348012345678" not in message
    assert message.count("redacted") >= 5


def test_log_store_is_bounded_filterable_and_monotonic() -> None:
    store = OperationalLogStore(capacity=2)
    first = store.append("INFO", "avera.http", "first")
    second = store.append("WARNING", "avera.http", "second")
    third = store.append("ERROR", "avera.worker", "third")

    assert (first.id, second.id, third.id) == (1, 2, 3)
    assert [item.id for item in store.records_after()] == [2, 3]
    assert [item.id for item in store.records_after(level="error")] == [3]
    assert [item.id for item in store.records_after(query="http")] == [2]
    assert store.stats()["retained"] == 2


def test_observe_page_status_and_sanitized_request_logs(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("AVERA_OBSERVABILITY_MODEL", raising=False)

    with TestClient(create_app()) as client:
        page = client.get("/observe")
        assert page.status_code == 200
        assert "The backend, as it happens." in page.text
        assert page.headers["content-security-policy"].startswith("default-src 'self'")
        assert page.headers["x-robots-tag"] == "noindex, nofollow"
        assert client.get("/observe/styles.css").status_code == 200
        assert client.get("/observe/app.js").status_code == 200

        service_map = client.get("/service-map")
        assert service_map.status_code == 200
        assert service_map.headers["x-avera-request-id"]

        logging.getLogger("avera.tests").warning(
            "broker password=do-not-show Bearer signed.jwt.value"
        )
        logs = client.get("/observe/api/logs?limit=100")
        assert logs.status_code == 200
        records = logs.json()["records"]
        request_record = next(
            item for item in records if item["context"].get("path") == "/service-map"
        )
        assert request_record["context"]["status"] == 200
        assert request_record["context"]["duration_ms"] >= 0
        captured_warning = next(item for item in records if item["logger"] == "avera.tests")
        assert "do-not-show" not in captured_warning["message"]
        assert "signed.jwt.value" not in captured_warning["message"]

        warnings = client.get("/observe/api/logs?level=WARNING")
        assert warnings.status_code == 200
        assert all(item["level"] == "WARNING" for item in warnings.json()["records"])
        assert client.get("/observe/api/logs?level=NOPE").status_code == 422

        status = client.get("/observe/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["health"]["status"] == "ok"
        assert payload["kafka"]["provider"] == "memory"
        assert payload["logs"]["retained"] > 0


def test_observe_routes_do_not_pollute_their_own_log_stream(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_GHOST_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("AVERA_OBSERVABILITY_MODEL", raising=False)

    with TestClient(create_app()) as client:
        before = client.get("/observe/api/logs").json()["latest_id"]
        client.get("/observe/api/status")
        client.get("/observe/api/logs")
        after = client.get("/observe/api/logs").json()["latest_id"]

        assert after == before
