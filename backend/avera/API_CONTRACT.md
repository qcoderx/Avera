# Avera API contract — Module 1

The following routes are the shared interface from `private/Avera_Work_Split.pdf`. `GET /stream` emits the contract object types from `contracts.py` as ordered Server-Sent Events. Each SSE `id` is the monotonically increasing stream sequence; `event` is the contract type; `data` is one JSON `StreamRecord`. Clients may reconnect with `Last-Event-ID` and receive later records. Fixture replay and live mode use the same envelope.

| Method | Route | Purpose | Main caller |
|---|---|---|---|
| GET | `/stream` | Ordered SSE stream of contract objects | Incident Command Centre |
| GET | `/service-map` | Services, dependencies, and routes in the simulated bank | Observability Agent, Incident Command Centre |
| POST | `/hypotheses` | Submit a hypothesis set for Ghost Test evaluation | Observability Agent |
| POST | `/ghost-test/rerun` | Rerun original failing tests after a proposed fix | Resolution Agent |
| POST | `/twin/simulate` | Simulate a route or configuration change in the isolated twin | Blast Radius Agent |
| POST | `/admin/route` | Apply an approved route change to the simulated bank | Resolution Agent |
| POST | `/demo/start` | Start or replay the demo | Incident Command Centre |
| POST | `/demo/inject` | Inject the deterministic failure and decoy | Incident Command Centre |
| POST | `/demo/reset` | Reset the simulated bank to its known baseline | Incident Command Centre |
| POST | `/approvals/{id}` | Record approval or rejection of an SMS, config, or code action | Incident Command Centre |

## Shared wire rules

- JSON timestamps are ISO 8601 with an explicit UTC offset; fixture timestamps use `Z`.
- `TestResult.confidence` is a fraction in `[0, 1]` (for example `0.96` means 96%). The Ghost Test handoff threshold is `0.85`.
- `BlastRadius.customers` contains tokenised customer identifiers. `customers_complete` is false when the fixture/API returns a display sample instead of the full affected set.
- Approval decisions require both `approver` and `decided_at`; pending approvals have neither.
- `ResolutionEvent` records what was prepared or applied. It never represents a transfer, reversal, or other movement of customer funds.
- Contract payloads reject undeclared fields so accidental interface drift is visible during development.

## Fixture

`fixtures/full_incident_stream.json` is a recorded, ordered stream for the injected Gateway B acknowledgement failure and simultaneous app-release decoy. It includes complaints, incident, competing hypotheses, test results, fix plan, blast radius, approval decisions, and resolution events. Customer tokens in the fixture are synthetic.
