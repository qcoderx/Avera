# Avera Incident Command Centre

Avera is an incident-intelligence platform for digital banking. It correlates operational signals and customer reports, tests competing explanations, and presents human-reviewed resolution actions in one command centre.

## High-level system design (target)

![Avera high-level system design](Avera_High_Level_System_Design.png)

External operational and customer signals enter through an API gateway and Kafka event backbone. The platform correlates signals into incidents, keeps incident and workflow state, and orchestrates accountable agents through a controlled AI gateway. Evidence, audit logs, and sandboxed changes support human review in the command panel.

## Current MVP

The current implementation is a single-bank simulation, not a live banking integration. It includes simulated incident traffic, an ordered event API and SSE stream, deterministic Ghost Tests, an isolated route twin, evidence-scoped blast-radius analysis, in-platform approvals, and simulated route changes. PostgreSQL and Kafka/Redpanda can back the canonical event stream; local development defaults to in-memory adapters. Approval, audit, and remediation state are still in memory, so this is not yet production-deployable for a bank.

Shaz's Observability Agent parses reported complaint facts, detects behavioural signals and incident changes, maps suspect components, generates competing hypotheses, and hands validated candidates to Ghost Test. Validated results now flow into remediation, which publishes the plan, blast radius, workflow approvals, and resolution events on the same stream. See [setup, run commands, evidence boundaries, and fixture limitations](docs/observability.md) and the [API contract and production persistence boundary](backend/avera/API_CONTRACT.md).

Ghost Test can optionally use GPT-5.4 Mini to summarize aggregate test results. The model does not choose the verdict, score hypotheses, approve actions, or change routes. External source/platform connections shown in the design remain integration work.

## Run locally

Start the backend:

```bash
cd backend
python -m pip install -r requirements.txt
PYTHONPATH=. uvicorn avera.api.app:app --reload
```

Start the frontend in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### Live API

The frontend talks to the deployed API at `https://api.avera.clippit.click` by default: the Vite dev and preview servers proxy `/api/*` to it, so the browser stays same-origin. To use a local backend instead, set `AVERA_API_PROXY_TARGET=http://127.0.0.1:8766` in `frontend/.env`. A build hosted on another origin can call the API directly with `VITE_AVERA_API_BASE`, provided the API lists that origin in `AVERA_CORS_ORIGINS`.

Every page renders the live `/stream`: incident, hypotheses, Ghost Test results, blast radius, fix plan, approvals and resolution events. The demo controller (`D`) starts a baseline, injects the failure, resets, investigates or replays the recorded stream. Approval routes are intentionally public for this simulated MVP; the server derives the workflow role and records decisions as `demo-platform-operator`. If the API is unreachable, the command centre falls back to the recorded fixture and retries the connection. Add `?mode=fixture` to force the recorded story.

Build the frontend for production:

```bash
cd frontend
npm run build
```

The command centre includes incident health and feed, service map, hypotheses and Ghost Test runs, customer impact, fix plan, approvals, timeline, and institutional memory. The service map is a Three.js story stage driven by the demo step. It shows the 1,109 affected customers as a crowd that turns coral as transfers fail silently, with the 13 complainers raised. The broken Gateway B acknowledgement is called out on the link itself, and hypothesis chips carry live confidence bars. A digital twin rises above the board while Ghost Tests run, and the reroute arc is locked until an engineer approves. A story bar, agent pipeline and timeline narrate each moment. The view is square to the board and only moves when you drag, scroll or use the zoom buttons. It falls back to the flat map when WebGL is unavailable. Add `?view=map&step=3` to the URL to open a specific view and step. The demo controller opens from the header or with the `D` key.

Run the backend regression suite, including the integrated observability/remediation path:

```bash
cd backend
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

For complaint parsing and hypothesis investigation, set `OPENAI_API_KEY` and `AVERA_OBSERVABILITY_MODEL=gpt-5.4-mini` in the backend environment. Optional Ghost Test summaries use `AVERA_GHOST_SUMMARY_MODEL`; without it, summaries use the deterministic local fallback.

## Safety boundary

All bank, customer, payment, and SMS data in the demo is synthetic. The API does not move real funds, contact real customers, send SMS, or execute production changes. Approval and route APIs operate only on the simulated bank.
