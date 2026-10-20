"""Run Shaz's integration against the existing simulated bank and Ghost Test."""
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
from .model import configured_model
from .orchestration import ObservabilityAgent
from .replay import replay_observations


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--until", default="2026-10-20T10:44:59+00:00")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    model = configured_model()
    bank = SimulatedBank()
    stream = EventStreamService(InMemoryEventRepository(), InMemoryEventBus())
    agent = ObservabilityAgent(stream, GhostTestAgent(bank, stream), bank.service_map, model)
    stream.observation_handlers.append(agent.observe)
    try:
        if args.fixture:
            until = datetime.fromisoformat(args.until)
            path = Path(__file__).resolve().parents[1] / "fixtures/full_incident_stream.json"
            for item in replay_observations(path, until):
                await stream.ingest("event" if hasattr(item, "service") else "complaint", item.model_dump(mode="json"))
            result = await agent.run(until=until)
        else:
            traffic = TrafficGenerator(bank)
            traffic.generate_healthy_baseline()
            FailureInjector(bank).inject()
            traffic.generate_incident_traffic(transactions=24, customers=16, complaint_count=1,
                retry_customers=3, history_refresh_customers=4, channel_switch_customers=2)
            for item in sorted([*bank.events, *bank.complaints], key=lambda x: (x.ts,x.id)):
                await stream.ingest("event" if hasattr(item,"service") else "complaint", item.model_dump(mode="json"))
            result = await agent.run()
        print(json.dumps({"scope": "Shaz observability -> existing Ghost Test (synthetic data)",
            "state": result["state"], "hypotheses": len(result["hypotheses"]), "errors": result["errors"],
            "verdicts": [{"zone": v.hypothesis.zone, "verdict": v.test_result.verdict.value}
                         for v in agent.feedback.evaluations] if agent.feedback else []}))
        return 0 if result["state"] == "tested" or (args.fixture and result["state"] == "insufficient_evidence") else 1
    finally:
        if model is not None:
            await model.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
