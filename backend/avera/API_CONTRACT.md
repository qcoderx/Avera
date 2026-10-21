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
| POST | `/demo/start` | Start or replay the demo | Incident Command Centre |
| POST | `/demo/inject` | Inject the deterministic failure and decoy | Incident Command Centre |
| POST | `/demo/reset` | Reset the simulated bank to its known baseline | Incident Command Centre |
| GET | `/remediation/{incident_id}` | Read the authenticated incident plan, blast radius, approval IDs, and resolution timeline | Incident Command Centre |
| GET | `/approvals` | List pending/final approvals for the authenticated user's role | Approver UI |
| GET | `/approvals/{id}` | Read an approval visible to the authenticated user's role | Approver UI |
| POST | `/approvals/{id}` | Record the authenticated approver's decision | Approver UI |

## Shared wire rules

- JSON timestamps are ISO 8601 with an explicit UTC offset; fixture timestamps use `Z`.
- `TestResult.confidence` is a fraction in `[0, 1]` (for example `0.96` means 96%). The Ghost Test handoff threshold is `0.85`.
- `BlastRadius.customers` contains tokenised customer identifiers. `customers_complete` is false when the fixture/API returns a display sample instead of the full affected set.
- Approval decisions require both `approver` and `decided_at`; pending approvals have neither.
- Approval routes require a signed HS256 JWT from the configured issuer/audience. Required claims are `sub`, `role`, `iss`, `aud`, `iat`, and `exp`; roles are `engineer` and `customer_dispute`. The client cannot set the approver identity or role in the request body.
- `POST /dev/auth/token` is a development-only local token issuer. It requires explicit development mode and opt-in, accepts loopback requests only, returns 10-minute tokens with `Cache-Control: no-store`, and must remain disabled outside local development. Production JWTs come from the bank identity provider.
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
- Approval records are created by the validated remediation workflow, not by a client-supplied approval payload. The platform verifies role, requester separation, expiry, and the payload hash before accepting a JWT-authenticated decision. An approved config decision drives the simulated route adapter; there is no standalone unauthenticated route-mutation endpoint.
- SMS, code, and reconciliation decisions are recorded in-platform. The MVP uses simulated gateways and does not connect to external SMS, source-control, bank, or reconciliation systems.
- Existing-platform connection buttons are integration entry points for the UI; this backend records decisions internally and does not yet authenticate to, send to, or execute actions in external platforms.
- The Vite development server proxies `/api/*` to FastAPI and strips the `/api` prefix, including for `/api/stream` SSE. Browser development uses same-origin proxying rather than permissive backend CORS.
- `POST /demo/start` and `/demo/reset` generate a 30-transfer healthy baseline. `/demo/inject` generates the default 1,284-transfer Bank X incident (thousands of streamed synthetic events) and is idempotent until the next reset. Counts can be adjusted within request bounds for smaller tests.
- Reset advances the simulator event-time epoch so repeated demo runs retain unique stream deduplication keys.

## Observability integration (Shaz)

`GET /observability` returns investigation state, parsed complaints, shared Incident
and Hypothesis outputs, and actual Ghost Test feedback.
`POST /observability/investigate` runs or waits for investigation.
`POST /observability/replay?until=<timezone-aware ISO timestamp>` investigates only
allowlisted fixture observations at or before that cutoff. The fixture lacks
healthy comparator activity, so evidence-only replay reports insufficient evidence.
`POST /demo/inject` schedules investigation automatically; the worker coalesces
new arrivals while a model run is active. A validated Ghost Test result is
handed to remediation only after its exact observed route, destination, error,
and transfer evidence agree. Remediation publishes fix plan, blast radius,
approval, and resolution records onto the same ordered stream.
See `docs/observability.md` for configuration and current limits.

## Runtime persistence boundary

PostgreSQL and Kafka/Redpanda currently back the canonical event stream when
configured. Approval, audit-chain, and remediation-case repositories are still
in-memory adapters in this MVP. The JWT-protected flow and simulator are
integration-tested, but production rollout requires durable PostgreSQL
repositories and a transactional outbox for remediation events before a restart
can safely preserve pending approvals or decisions. Kafka is currently the
outbound notification backbone; external source/webhook events enter through
`POST /ingest/{source}` rather than a Kafka input consumer.

## Full-story UI fixture

`fixtures/full_incident_stream.json` is a recorded, ordered stream for the injected Gateway B acknowledgement failure and simultaneous app-release decoy. It includes complaints, incident, competing hypotheses, test results, fix plan, blast radius, approval decisions, and resolution events. Customer tokens in the fixture are synthetic.
