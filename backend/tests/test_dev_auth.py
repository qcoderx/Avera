"""The local UI token issuer is opt-in, loopback-only, and uses the normal JWT verifier."""
import asyncio

import httpx
import jwt

from avera.api.app import create_app


def test_local_dev_auth_is_loopback_only_and_issues_normal_short_lived_approver_jwt(monkeypatch):
    secret = "test-only-local-signing-secret-at-least-32-bytes"
    monkeypatch.setenv("AVERA_APPROVER_JWT_SECRET", secret)
    monkeypatch.setenv("AVERA_APPROVER_JWT_ISSUER", "avera-test")
    monkeypatch.setenv("AVERA_APPROVER_JWT_AUDIENCE", "avera-test-api")
    monkeypatch.setenv("AVERA_ENVIRONMENT", "production")
    monkeypatch.setenv("AVERA_DEV_AUTH_ENABLED", "true")
    app = create_app()

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 45000)),
            base_url="http://testserver",
        ) as local_client:
            disabled = await local_client.post("/dev/auth/token", json={"role": "engineer"})
            assert disabled.status_code == 404

            monkeypatch.setenv("AVERA_ENVIRONMENT", "development")
            issued = await local_client.post("/dev/auth/token", json={"role": "engineer"})
            assert issued.status_code == 200, issued.text
            assert issued.headers["cache-control"] == "no-store"
            body = issued.json()
            claims = jwt.decode(body["access_token"], secret, algorithms=["HS256"],
                                issuer="avera-test", audience="avera-test-api")
            assert body["expires_in"] == 600 and body["role"] == "engineer"
            assert claims["sub"] == "local-dev-engineer" and claims["role"] == "engineer"

            approvals = await local_client.get(
                "/approvals", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
            assert approvals.status_code == 200

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 45000)),
            base_url="http://testserver",
        ) as remote_client:
            remote = await remote_client.post("/dev/auth/token", json={"role": "engineer"})
            assert remote.status_code == 404

    asyncio.run(scenario())
