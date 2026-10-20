# Avera backend

The backend is fixture-ready in local mode and can use PostgreSQL plus Kafka/Redpanda when both are configured.

```bash
cd backend
python -m pip install -r requirements.txt
PYTHONPATH=. uvicorn avera.api.app:app --reload
```

Run the backend demo-readiness regression (ten complete seeded cycles) with:

```bash
cd backend
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Without environment configuration, the API uses in-memory storage and event notifications. To enable durable mode, set both `AVERA_DATABASE_URL` and `AVERA_KAFKA_BOOTSTRAP_SERVERS`. PostgreSQL assigns stream sequence numbers and stores the append-only replay log; Kafka/Redpanda carries canonical stream notifications and failed deliveries go to `avera.stream.dlq.v1`. Create `avera.stream.v1` with one partition for this single-bank demo. The API uses a stable bank key, while SSE always reads ordered rows from PostgreSQL, including after reconnects.

Useful routes:

- `POST /replay` loads the bundled synthetic incident fixture.
- `POST /ingest/{source}` validates and stores one typed contract object.
- Simulated bank events and complaints are queued into the same ingestion pipeline automatically.
- `POST /hypotheses` runs deterministic suspect/control tests and streams their results.
- `POST /ghost-test/rerun` verifies the current simulated route against the last tested hypothesis.
- `POST /twin/simulate` compares a candidate route against the current route in detached simulator copies.
- `POST /approvals` creates an in-platform pending request; `POST /approvals/{id}` records approve/reject decisions. `POST /admin/route` applies only a matching approved engineer config change to the simulated bank.
- `GET /approvals` and `GET /approvals/{id}` back the in-platform approval queue and decision detail. External-platform connection buttons are UI hooks only in this module; no external credentials or actions are wired.
- `POST /demo/start`, `/demo/inject`, and `/demo/reset` run the healthy baseline, thousands-event synthetic incident, and reset lifecycle.
- `GET /stream` streams records; reconnect with `Last-Event-ID` or `?after=<sequence>`.
- `GET /service-map` returns the simulated bank topology and current routes.
- `GET /health` reports adapter mode and in-process delivery counters.

The simulator and all fixture identifiers are synthetic. No real bank, customer, SMS, or payment system is contacted. SMS and code approval records are audit/demo objects only; this API does not dispatch messages or execute code.
