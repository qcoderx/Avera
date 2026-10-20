# Shaz's Observability Agent

This component uses `avera.contracts`, `EventStreamService`, the simulator's service
map, and the existing `GhostTestAgent.evaluate(HypothesisSubmission)` integration.
It does not change shared schemas, Ghost Test scoring, remediation, or the UI.

## Run from the repository root (PowerShell)

Use Python 3.11 or newer (the existing shared contracts require it). Create a
local virtual environment if `.venv` does not exist. From the repository root:

```powershell
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
if (!(Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

Edit **your local `backend/.env`** and fill in `OPENAI_API_KEY` with an authorized
key obtained through the team's private credential-sharing channel. The example
already specifies the OpenAI endpoint and `gpt-5.4-mini` model. Keep the key out of
Git and project documentation. Existing local `.env` files are preserved by the
command above; add the example's non-secret settings to yours if needed.

Then check setup and run the agent:

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m avera.observability.smoke
./.venv/Scripts/python.exe -m pytest backend/tests/test_observability.py -q
./.venv/Scripts/python.exe -m avera.observability.demo
```

Expected: the smoke test prints `API authentication and model access: PASSED`;
the focused suite reports `14 passed`; the synthetic demo prints JSON with
`state: "tested"`, at least three competing candidates, and Ghost Test verdicts.
The release candidate is tested by Ghost Test. Model/API failures are reported
explicitly rather than replaced with canned hypotheses.

To serve the agent through the existing backend instead:

```powershell
./.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
./.venv/Scripts/python.exe -m uvicorn avera.api.app:app --app-dir backend --env-file backend/.env --reload
```

The ignored `backend/.env` uses the shared `OPENAI_API_KEY` and optional
`OPENAI_BASE_URL`, exactly as the existing OpenAI SDK client does. Its absence
uses the existing OpenAI SDK endpoint. The local model selection follows the
repository's documented `gpt-5.4-mini` configuration. No key-prefix inference is
used. No secret belongs in `.env.example` or a commit.

Observability accepts the existing `AVERA_GHOST_SUMMARY_MODEL` model setting;
`AVERA_OBSERVABILITY_MODEL` can explicitly override it for this language task.
One key in one local file serves both compatible clients. No other agent is
refactored. An incompatible, non-OpenAI provider would require a separate adapter;
this implementation does not guess its protocol from a credential.

`POST /demo/start` collects the healthy baseline. `POST /demo/inject` schedules
investigation after simulator ingestion flushes. `GET /observability` exposes
state, parsed complaints, Incident, Hypotheses, and returned Ghost Test feedback.
`POST /observability/investigate` explicitly runs/waits for an investigation.
Incident/Hypothesis/TestResult records also use the existing ordered stream.
`POST /demo/reset` cancels pending work and clears investigation state.

Run a small synthetic model-to-Ghost-Test integration without starting a server:

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m avera.observability.demo
```

Run the one-request, non-customer authentication/model-access check:

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m avera.observability.smoke
```

## Evidence and decision boundary

Only allowlisted observed events and raw complaints enter the investigator.
Source IDs are preserved. Hidden fault-injector events, labels, expected answers,
fix records and initial TestResults cannot enter the investigation. Payloads are
projected onto explicit observation fields; customer reports have any supplied
`parsed` value discarded before model parsing.

The complaint model extracts reported issue/product/amount/time/reference only.
Python validates amounts against reported numbers, leaves ambiguous times null,
and checks transaction references against observed customer identity and time.
No missing terminal event is interpreted as a failed or settled payment.

Python deduplicates by observation type and source ID, rejects conflicting
identities, and counts observations arriving beyond the two-window retention
horizon as late. Delayed observations within that horizon enter event-time
correlation. Five-minute current and preceding windows are used. For the short
simulator demo, available successful terminals before the first failure form an
explicitly labelled cross-route comparator when a full prior window is absent.
At least three comparator transactions and three current failures in a correlated
group, with a failure fraction increase exceeding 0.10, are required. These are code-level
MVP detection rules, not inferred validation confidence. A route-matched baseline
is preferable; its absence remains explicit in the fingerprint.

Retries, repeated history refreshes, channel switching and explicit abandonment
use actual observations. Silence alone never establishes abandonment. Correlation
groups transaction traces by route, gateway, destination and app version, with
ordered service/error evidence retained in the fingerprint. Suspects are mapped
to topology nodes. The release-webhook `mobile-app` vocabulary maps to the
topology's `customer-app` node and existing Ghost Test `mobile_app.release` zone.
Resolver tags follow the work-split vocabulary and are checked against a local
registry; shared contracts currently have no resolver enumeration.

Versioned prompts live in `avera/observability/prompts.py`, matching the existing
Python prompt style in `avera.llm`. Model candidates require exact supplied
evidence IDs, component evidence, valid zone/resolver tags, concrete claims and
falsification conditions. When enough components are observed, at least three
distinct zones are required, including the simultaneous release. Python assigns
stable ranks by unique support count minus opposition count, then zone/claim/ID.
These ranks are preliminary only; Ghost Test determines all verdicts and scores.

Each structured request has two attempts, a 30-second SDK timeout and a 35-second
outer bound. Complaint parsing has concurrency four. No canned model reasoning
is substituted on failure: sanitized diagnostics and `degraded`/`error` state
remain visible. Malformed candidates receive a bounded correction attempt.
Ghost Test feedback identities are checked; inconclusive sets allow one revision,
then `escalated` state exposes the evidence and remaining uncertainty for engineer
review. Rejected explanations cannot be resubmitted unchanged. TestResults enter
model context only after the actual feedback handoff. Terminal results are cached
until demo reset, including degraded states, to prevent repeated API charges and
duplicate incident records from repeated inject calls.

## Replay and known integration limits

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m avera.observability.demo --fixture --until 2026-10-20T10:44:59+00:00
```

The API equivalent is `POST /observability/replay?until=2026-10-20T10:44:59Z`.
Replay filters record type, payload fields and event-time cutoff before handing
observations to the agent. It deliberately does not call the UI's full-story
`POST /replay`, which includes prewritten answers and future resolution records.

**Fixture blocker:** the supplied full-story fixture contains only two timeout
events, a deployment, aggregate behavioural alerts and complaints; it provides no
healthy comparator activity. Its evidence-only replay therefore returns
`insufficient_evidence` rather than copying the fixture's expected hypotheses.
Koded needs to supply baseline/transaction observations to demonstrate initial
inference entirely from that fixture. The existing simulator's healthy-then-fault
path provides those observations and supports the working integration demo.

Ghost Test currently dispatches experiments by zone, rather than interpreting
arbitrary natural-language disproof programs. The agent uses that vocabulary and
leaves execution/scoring with Ghost Test. Engineer escalation is a visible state
and report; no external messages are sent. Durable restart recovery, multi-incident
lifecycle management, and other banking-domain detectors are beyond this MVP.
Adam's downstream port wiring and Platinum's UI were not modified or certified
by the observability integration test.

## Checks

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m pytest backend/tests -q
```

For only this component, use
`./.venv/Scripts/python.exe -m pytest backend/tests/test_observability.py -q`.
The full pre-existing remediation suite additionally requires `tzdata` on Windows;
it was installed locally for the full regression run without changing its code.

`tests/test_observability.py` uses an explicitly labelled language-model test
double and the real local Ghost Test, with separate inconclusive feedback tests.
It covers healthy activity, injected failure/release, duplicate/delayed/conflicting
events, unknown outcomes, amount and identity checks, valid shared outputs and
references, malformed model output, timeout/API failures, bounded escalation,
evidence-only fixture replay and reset. Model access is verified separately by
the real smoke test; the small synthetic integration can also use the real model.
