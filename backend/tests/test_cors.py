"""Cross-origin browser access is explicit and narrowly scoped."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avera.api.app import RuntimeConfigurationError, create_app
from avera.events.bus import InMemoryEventBus
from avera.events.repository import InMemoryEventRepository


FRONTEND = "https://avera-lilac.vercel.app"


def _client() -> TestClient:
    return TestClient(
        create_app(repository=InMemoryEventRepository(), bus=InMemoryEventBus())
    )


def test_cors_is_closed_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AVERA_CORS_ORIGINS", raising=False)

    with _client() as client:
        response = client.get("/health", headers={"Origin": FRONTEND})

    assert "access-control-allow-origin" not in response.headers


def test_configured_frontend_can_preflight_approval_requests(monkeypatch) -> None:
    monkeypatch.setenv("AVERA_CORS_ORIGINS", f"{FRONTEND}, https://ops.example")

    with _client() as client:
        response = client.options(
            "/approvals",
            headers={
                "Origin": FRONTEND,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == FRONTEND
        assert "Content-Type" in response.headers["access-control-allow-headers"]
        assert "access-control-allow-credentials" not in response.headers

        health = client.get("/health", headers={"Origin": FRONTEND})
        assert health.headers["access-control-allow-origin"] == FRONTEND
        exposed = health.headers["access-control-expose-headers"].casefold()
        assert "x-avera-request-id" in exposed


def test_unlisted_origin_receives_no_cors_permission(monkeypatch) -> None:
    monkeypatch.setenv("AVERA_CORS_ORIGINS", FRONTEND)

    with _client() as client:
        response = client.get("/health", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "avera-lilac.vercel.app",
        "https://avera-lilac.vercel.app/path",
        "https://user:password@avera-lilac.vercel.app",
    ],
)
def test_unsafe_or_non_origin_values_are_rejected(monkeypatch, origins: str) -> None:
    monkeypatch.setenv("AVERA_CORS_ORIGINS", origins)

    with pytest.raises(RuntimeConfigurationError):
        create_app(repository=InMemoryEventRepository(), bus=InMemoryEventBus())
