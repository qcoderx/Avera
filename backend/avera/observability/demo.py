import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from avera.events.bus import InMemoryEventBus
from avera.events.repository import InMemoryEventRepository
from avera.events.service import EventStreamService
from avera.ghost_test import GhostTestAgent
from avera.simulation.bank import SimulatedBank
from avera.simulation.failures import FailureInjector
from avera.simulation.traffic import TrafficGenerator
from .model import configured_model, configured_model_name, configured_base_url
from .prompts import PROMPT_VERSION
from .orchestration import ObservabilityAgent
from .replay import replay_observations


async def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--until", default="2026-10-20T10:44:59+00:00")
    parser.add_argument("--observability-only", action="store_true", help="Return ranked hypotheses without executing Ghost Test")
    parser.add_argument("--healthy", action="store_true", help="Run healthy baseline activity only")
    parser.add_argument("--output", type=Path, help="Save full results and sanitized source evidence as JSON")
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    model = configured_model()
    bank = SimulatedBank()
    stream = EventStreamService(InMemoryEventRepository(), InMemoryEventBus())
    ghost = None if args.observability_only else GhostTestAgent(bank, stream)
    agent = ObservabilityAgent(stream, ghost, bank.service_map, model)
    stream.observation_handlers.append(agent.observe)
    try:
        if args.fixture:
            until = datetime.fromisoformat(args.until)
            path = Path(__file__).resolve().parents[1] / "fixtures/full_incident_stream.json"
            for item in replay_observations(path, until):
                await stream.ingest("event" if hasattr(item, "service") else "complaint", item.model_dump(mode="json"))
            result = await agent.run(until=until, handoff=not args.observability_only)
        else:
            traffic = TrafficGenerator(bank)
            traffic.generate_healthy_baseline()
            if not args.healthy:
                FailureInjector(bank).inject()
                traffic.generate_incident_traffic(transactions=24, customers=16, complaint_count=1,
                    retry_customers=3, history_refresh_customers=4, channel_switch_customers=2)
            for item in sorted([*bank.events, *bank.complaints], key=lambda x: (x.ts,x.id)):
                await stream.ingest("event" if hasattr(item,"service") else "complaint", item.model_dump(mode="json"))
            result = await agent.run(handoff=not args.observability_only)
        if args.output:
            report = {"mode": "observability_only" if args.observability_only else "integrated",
                      "model": configured_model_name(), "endpoint": configured_base_url(), "prompt_version": PROMPT_VERSION,
                      "ghost_test_executed": agent.feedback is not None,
                      "result": result,
                      "source_observations": [v.model_dump(mode="json") for v in agent.items.values()]}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"scope": "Observability only" if args.observability_only else "Observability -> existing Ghost Test (synthetic data)",
            "ghost_test_executed": agent.feedback is not None,
            "state": result["state"], "hypotheses": len(result["hypotheses"]), "errors": result["errors"],
            "verdicts": [{"zone": v.hypothesis.zone, "verdict": v.test_result.verdict.value}
                         for v in agent.feedback.evaluations] if agent.feedback else []}))
        return 0 if result["state"] in {"tested", "hypotheses_ready", "healthy"} or (args.fixture and result["state"] == "insufficient_evidence") else 1
    finally:
        if model is not None:
            await model.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
