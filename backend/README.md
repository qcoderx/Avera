# Avera backend

FastAPI services for the simulated bank, incident event stream, Ghost Test, isolated route twin, and approval-gated demo controls.

## Run

```bash
python -m pip install -r requirements.txt
PYTHONPATH=. uvicorn avera.api.app:app --reload
```

The API defaults to in-memory storage and event delivery. For durable mode, configure both `AVERA_DATABASE_URL` and `AVERA_KAFKA_BOOTSTRAP_SERVERS`. PostgreSQL stores the ordered stream; Kafka/Redpanda carries event notifications. For this single-bank demo, create `avera.stream.v1` with one partition.

## Test

```bash
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

The suite includes ten seeded incident cycles and checks that routing requires approval, the twin stays isolated, Ghost Test scores remain stable, and reset restores the baseline.

## Main API

- `POST /demo/start`, `/demo/inject`, `/demo/reset` — run the healthy baseline, inject the synthetic incident, or reset.
- `POST /hypotheses`, `POST /ghost-test/rerun` — evaluate hypotheses and verify a simulated fix.
- `POST /twin/simulate`, `POST /admin/route` — compare a candidate route in an isolated twin and apply a matching approved config to the simulated bank.
- `POST /approvals`, `GET /approvals`, `POST /approvals/{id}` — create, list, and decide in-platform approval requests.
- `GET /stream`, `GET /service-map`, `GET /health` — read ordered events, simulated topology, and service health.
- `POST /ingest/{source}`, `POST /replay` — ingest typed events or replay the bundled incident fixture.

## Optional GPT summary

Ghost Test scoring and routing remain deterministic. To enable an optional model-generated summary, configure `OPENAI_API_KEY` and `AVERA_GHOST_SUMMARY_MODEL=gpt-5.4-mini`. The model receives only aggregate test measurements and has no tools or authority to alter results. If it is unavailable, the API returns a local summary instead.

No real bank, customer, payment, SMS, or external approval platform is contacted by this MVP.
