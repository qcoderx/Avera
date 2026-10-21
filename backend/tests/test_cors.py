from fastapi.testclient import TestClient

from avera.api.app import create_app
from avera.events.bus import InMemoryEventBus
from avera.events.repository import InMemoryEventRepository


def _client():
    return TestClient(create_app(repository=InMemoryEventRepository(), bus=InMemoryEventBus()))


def test_cors_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("AVERA_CORS_ORIGINS", raising=False)
    with _client() as client:
        response = client.get("/health", headers={"Origin": "https://command.example"})
        assert "access-control-allow-origin" not in response.headers


def test_cors_allows_only_listed_origins(monkeypatch):
    monkeypatch.setenv("AVERA_CORS_ORIGINS", "https://command.example, https://ops.example")
    with _client() as client:
        allowed = client.options("/approvals", headers={
            "Origin": "https://command.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        })
        assert allowed.headers["access-control-allow-origin"] == "https://command.example"
        blocked = client.get("/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in blocked.headers
