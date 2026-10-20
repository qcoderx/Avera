# Avera API contract — Module 1

The following routes are the shared interface from `private/Avera_Work_Split.pdf`. `GET /stream` emits the contract object types from `contracts.py` as ordered Server-Sent Events. Each SSE `id` is the monotonically increasing stream sequence; `event` is the contract type; `data` is one JSON `StreamRecord`. Clients may reconnect with `Last-Event-ID` and receive later records. Fixture replay and live mode use the same envelope.

| Method | Route | Purpose | Main caller |
|---|---|---|---|
| GET | `/stream` | Ordered SSE stream of contract objects | Incident Command Centre |
| GET | `/service-map` | Services, dependencies, and routes in the simulated bank | Observability Agent, Incident Command Centre |
| POST | `/ingest/{source}` | Validate, normalize, persist, and publish a typed source event | Bank/webhook adapters |
| POST | `/replay` | Replay the recorded full-incident fixture into the current stream | Incident Command Centre |
| POST | `/hypotheses` | Submit a hypothesis set for Ghost Test evaluation | Observability Agent |
| POST | `/ghost-test/rerun` | Rerun original failing tests after a proposed fix | Resolution Agent |
| POST | `/twin/simulate` | Simulate a route or configuration change in the isolated twin | Blast Radius Agent |
| POST | `/admin/route` | Apply an approved route change to the simulated bank | Resolution Agent |
| POST | `/demo/start` | Start or replay the demo | Incident Command Centre |
| POST | `/demo/inject` | Inject the deterministic failure and decoy | Incident Command Centre |
| POST | `/demo/reset` | Reset the simulated bank to its known baseline | Incident Command Centre |
| POST | `/approvals` | Create an in-platform pending approval request | Resolution Agent |
| GET | `/approvals` | List in-platform approvals, optionally filtered by status | Incident Command Centre |
| GET | `/approvals/{id}` | Read one approval and its decision state | Incident Command Centre |
| POST | `/approvals/{id}` | Record approval or rejection of an SMS, config, or code action | Incident Command Centre |

## Shared wire rules

- JSON timestamps are ISO 8601 with an explicit UTC offset; fixture timestamps use `Z`.
- `TestResult.confidence` is a fraction in `[0, 1]` (for example `0.96` means 96%). The Ghost Test handoff threshold is `0.85`.
- `BlastRadius.customers` contains tokenised customer identifiers. `customers_complete` is false when the fixture/API returns a display sample instead of the full affected set.
- Approval decisions require both `approver` and `decided_at`; pending approvals have neither.
- `ResolutionEvent` records what was prepared or applied. It never represents a transfer, reversal, or other movement of customer funds.
- Contract payloads reject undeclared fields so accidental interface drift is visible during development.
- PostgreSQL assigns the authoritative sequence; the stream repository deduplicates by `(type, object id, occurred_at)` so repeated incident lifecycle updates remain distinct while fixture replay is idempotent.
- PostgreSQL append transactions take a stream-scoped advisory lock so allocated sequence order matches commit order for the single-bank demo.
- Kafka/Redpanda is the live notification bus and DLQ; SSE reconnect/replay reads from PostgreSQL, so broker notifications never define client ordering.
- In the single-bank demo, canonical Kafka records use a stable bank key. SSE reads persisted records in sequence order and tolerates duplicated or coalesced broker wake-ups.
- Configure both `AVERA_DATABASE_URL` and `AVERA_KAFKA_BOOTSTRAP_SERVERS` for PostgreSQL/Kafka mode. With neither configured, the API uses in-memory adapters for local development.
- `POST /hypotheses` emits a `Hypothesis` and its `TestResult` to `/stream`; confidence at or above `0.85` is eligible for handoff, otherwise `send_back_to_observability` is true.
- The `/hypotheses` response includes a short `summary`, `summary_source`, and optional `summary_model`. Without explicit model configuration this is generated locally. An optional GPT summary is advisory only; deterministic `TestResult` confidence/verdict fields control handoff and are never modified by the model.
- `POST /ghost-test/rerun` uses the current simulated Bank X route in an isolated copy. It never mutates the live bank or moves real funds.
- `POST /twin/simulate` compares the current route with a candidate route using detached simulator copies; it never changes the live route.
- Approval flow is two-step: `POST /approvals` creates a pending record, then `POST /approvals/{id}` records an in-platform decision. Only an approved engineer-level `config` request with a matching `action=reroute`, `destination_bank`, and `to_gateway` can be applied by `POST /admin/route`.
- `POST /admin/route` accepts only the approval ID, emits route and resolution records, and changes only the simulated bank. SMS/code approvals are recorded for review but are not executed by this demo API.
- Existing-platform connection buttons are integration entry points for the UI; this backend records decisions internally and does not yet authenticate to, send to, or execute actions in external platforms.
- `POST /demo/start` and `/demo/reset` generate a 30-transfer healthy baseline. `/demo/inject` generates the default 1,284-transfer Bank X incident (thousands of streamed synthetic events) and is idempotent until the next reset. Counts can be adjusted within request bounds for smaller tests.
- Reset advances the simulator event-time epoch so repeated demo runs retain unique stream deduplication keys.

## Observability integration (Shaz)

`GET /observability` returns investigation state, parsed complaints, shared Incident
and Hypothesis outputs, and actual Ghost Test feedback.
`POST /observability/investigate` runs or waits for investigation.
`POST /observability/replay?until=<timezone-aware ISO timestamp>` investigates only
allowlisted fixture observations at or before that cutoff. The fixture lacks
healthy comparator activity, so evidence-only replay reports insufficient evidence.
`POST /demo/inject` now schedules investigation automatically; start/reset clear
its state and cancel pending model work. No shared wire fields have changed.
See `docs/observability.md` for configuration and current limits.

## Full-story UI fixture

`fixtures/full_incident_stream.json` is a recorded, ordered stream for the injected Gateway B acknowledgement failure and simultaneous app-release decoy. It includes complaints, incident, competing hypotheses, test results, fix plan, blast radius, approval decisions, and resolution events. Customer tokens in the fixture are synthetic.
