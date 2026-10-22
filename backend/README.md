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

The hosted MVP API is `https://api.avera.clippit.click`. Frontend developers can set `AVERA_API_PROXY_TARGET=https://api.avera.clippit.click` in `frontend/.env.local` and continue calling same-origin `/api/*` routes through Vite. Deployed browser clients that call the API directly must be listed as exact, comma-separated origins in `AVERA_CORS_ORIGINS`; production currently allows `https://avera-lilac.vercel.app`. Wildcard origins are rejected so browser access stays explicitly scoped.

The hosted operational console is `https://api.avera.clippit.click/observe`. It shows bounded, current-process application and access logs plus PostgreSQL, Kafka, and outbox state. The public feed is deliberately sanitized: request and response bodies, headers, client addresses, stack traces, broker hosts, credentials, and customer phone numbers are never included. History resets when the API process restarts; use the VPS journal for private, durable forensic logs.

For durable mode, configure both `AVERA_DATABASE_URL` and `AVERA_KAFKA_BOOTSTRAP_SERVERS`. PostgreSQL stores the ordered stream and a transactional Kafka outbox; a background dispatcher retries unacknowledged notifications with bounded exponential backoff. On upgrade, legacy stream rows are queued once for delivery. Delivery is at-least-once, so downstream consumers should deduplicate by the event `sequence`. Kafka/Redpanda currently carries outbound event notifications, not inbound source ingestion.

For a managed Kafka provider that requires authenticated TLS, set `AVERA_KAFKA_SECURITY_PROTOCOL=SASL_SSL`, `AVERA_KAFKA_SASL_MECHANISM`, `AVERA_KAFKA_SASL_USERNAME`, `AVERA_KAFKA_SASL_PASSWORD`, and `AVERA_KAFKA_CA_FILE` in the server environment. The CA file must be readable by the service user. Keep credentials out of the repository; local Redpanda can use the default `PLAINTEXT` setting.

## Test

```bash
.venv/bin/python -m pytest -q
```

The suite exercises the seeded incident path, the observability-to-remediation handoff, public in-platform approvals, payload-bound decisions, and simulated route changes.

## Main API

- `POST /demo/run` — start a fresh baseline, inject the synthetic incident, and begin investigation in one request.
- `POST /demo/start`, `/demo/inject`, `/demo/reset` — control the baseline and injection stages separately when needed.
- `GET /bank-demo/state`, `POST /bank-demo/trigger`, `POST /bank-demo/reset` — run and inspect the contained duplicate-debit source-repair demo used by the frontend `/bank` route.
- `POST /hypotheses`, `POST /ghost-test/rerun` — evaluate hypotheses and verify a simulated fix.
- `POST /twin/simulate` — compare a candidate route in an isolated twin.
- `GET /remediation/{incident_id}` — read the simulated fix plan, blast radius, and resolution timeline.
- `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}` — read approvals and record a public simulated decision. The server derives the required workflow role, ignores client-supplied identity headers, and binds each decision to the current payload hash.
- `GET /stream`, `GET /service-map`, `GET /health` — read ordered events, simulated topology, and service health.
- `GET /observe` — open the live operational console. Supporting routes under `/observe/api/*` provide sanitized status, log history, and SSE updates to the page.
- `POST /ingest/{source}`, `POST /replay` — ingest typed events or replay the bundled incident fixture.

## GPT configuration

For complaint parsing and hypothesis investigation, configure `OPENAI_API_KEY` and `AVERA_OBSERVABILITY_MODEL=gpt-5.4-mini`. Optional Ghost Test summaries use `AVERA_GHOST_SUMMARY_MODEL`; scoring and routing remain deterministic.

The remediation domain uses in-process approval, audit, and case repositories while the API is running. In PostgreSQL mode, a versioned atomic snapshot row containing those aggregates and the simulated route is updated after each handoff, decision, and reset, then validated and restored at startup. The frontend reconciles durable stream history against `GET /approvals`, so an old event can never be presented as an actionable decision. External source events enter through `/ingest/{source}`; Kafka is currently the outbound event-notification bus.

No real bank, customer, payment, SMS, or external approval platform is contacted by this MVP. Approval and remediation routes have no authentication and must not be connected to production systems in this form.
