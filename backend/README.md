# Avera backend

FastAPI services for the simulated bank, incident event stream, Ghost Test, isolated route twin, and approval-gated demo controls.

## Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
set -a; [ ! -f .env ] || source .env; set +a
PYTHONPATH=. .venv/bin/python -m uvicorn avera.api.app:app --reload
```

The API defaults to in-memory storage and event delivery. The ignored local `backend/.env` in this checkout is configured for an isolated PostgreSQL test cluster at `127.0.0.1:55432` and a single-node Redpanda broker at `127.0.0.1:9092`. PostgreSQL data lives outside the repository under `~/Library/Application Support/Avera/postgres-test`; this cluster uses loopback-only trust authentication for local development and must not be exposed or reused for production data. Docker Desktop must be running for Redpanda. This machine already has the `avera.stream.v1` and `avera.stream.dlq.v1` topics, each with one partition.

For browser development, start the API on `127.0.0.1:8766` and the Vite app from `frontend/`. The Vite server proxies same-origin `/api/*` requests to the backend and strips the `/api` prefix; use `/api/stream` for SSE. Override `AVERA_API_PROXY_TARGET` in an ignored `frontend/.env.local` if the API runs elsewhere. No wildcard backend CORS is required.

The hosted MVP API is `https://api.avera.clippit.click`. Frontend developers can set `AVERA_API_PROXY_TARGET=https://api.avera.clippit.click` in `frontend/.env.local` and continue calling same-origin `/api/*` routes through Vite.

The hosted operational console is `https://api.avera.clippit.click/observe`. It shows bounded, current-process application and access logs plus PostgreSQL, Kafka, and outbox state. The public feed is deliberately sanitized: request and response bodies, headers, client addresses, stack traces, broker hosts, credentials, and customer phone numbers are never included. History resets when the API process restarts; use the VPS journal for private, durable forensic logs.

For durable mode, configure both `AVERA_DATABASE_URL` and `AVERA_KAFKA_BOOTSTRAP_SERVERS`. PostgreSQL stores the ordered stream and a transactional Kafka outbox; a background dispatcher retries unacknowledged notifications with bounded exponential backoff. On upgrade, legacy stream rows are queued once for delivery. Delivery is at-least-once, so downstream consumers should deduplicate by the event `sequence`. Kafka/Redpanda currently carries outbound event notifications, not inbound source ingestion.

For a managed Kafka provider that requires authenticated TLS, set `AVERA_KAFKA_SECURITY_PROTOCOL=SASL_SSL`, `AVERA_KAFKA_SASL_MECHANISM`, `AVERA_KAFKA_SASL_USERNAME`, `AVERA_KAFKA_SASL_PASSWORD`, and `AVERA_KAFKA_CA_FILE` in the server environment. The CA file must be readable by the service user. Keep credentials out of the repository; local Redpanda can use the default `PLAINTEXT` setting.

## Test

```bash
.venv/bin/python -m pytest -q
```

The suite exercises the seeded incident path, the observability-to-remediation handoff, JWT role enforcement, in-platform approvals, and simulated route changes.

## Main API

- `POST /demo/start`, `/demo/inject`, `/demo/reset` — run the healthy baseline, inject the synthetic incident, or reset.
- `POST /hypotheses`, `POST /ghost-test/rerun` — evaluate hypotheses and verify a simulated fix.
- `POST /twin/simulate` — compare a candidate route in an isolated twin.
- `GET /remediation/{incident_id}` — read the JWT-protected fix plan, blast radius, and resolution timeline.
- `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}` — read role-scoped approvals and record a JWT-authenticated decision. The API verifies the issuer, audience, expiry, subject, and `role` claim; it does not issue tokens.
- `GET /stream`, `GET /service-map`, `GET /health` — read ordered events, simulated topology, and service health.
- `GET /observe` — open the live operational console. Supporting routes under `/observe/api/*` provide sanitized status, log history, and SSE updates to the page.
- `POST /ingest/{source}`, `POST /replay` — ingest typed events or replay the bundled incident fixture.

## GPT and approver JWT configuration

For complaint parsing and hypothesis investigation, configure `OPENAI_API_KEY` and `AVERA_OBSERVABILITY_MODEL=gpt-5.4-mini`. Optional Ghost Test summaries use `AVERA_GHOST_SUMMARY_MODEL`; scoring and routing remain deterministic.

For approval decisions, configure `AVERA_APPROVER_JWT_SECRET` with at least 32 random bytes, plus the expected issuer and audience. JWTs must contain `sub`, `role`, `iss`, `aud`, `iat`, and `exp`; accepted roles are `engineer` and `customer_dispute`. In production, connect this verifier to the bank's identity provider and its key rotation policy.

Local UI development may opt in to `POST /dev/auth/token` by setting `AVERA_ENVIRONMENT=development` and `AVERA_DEV_AUTH_ENABLED=true`. The endpoint only issues 10-minute engineer/customer-dispute tokens to loopback callers and returns `Cache-Control: no-store`. It is disabled by default and must never be enabled in a deployed environment. The signing secret stays server-side; browser code receives only the short-lived bearer token. Production tokens must come from the bank identity provider.

The end-to-end remediation workflow currently uses in-memory approval, audit, and case repositories. PostgreSQL/Kafka configuration durably persists canonical stream events and their pending outbound notifications, but does not yet make remediation state durable. External source events enter through `/ingest/{source}`; Kafka is currently the outbound event-notification bus.

No real bank, customer, payment, SMS, or external approval platform is contacted by this MVP.
