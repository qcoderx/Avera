from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from datetime import datetime, timedelta

from avera.contracts import Complaint, Event, Incident, IncidentStatus
from avera.ghost_test import HypothesisSubmission
from avera.ghost_test import HypothesisEvaluationSet
from .behaviour_signals import behaviour_signals
from .complaint_parsing import parse_complaint
from .hypotheses import investigate
from .model import ModelUnavailable
from .replay import observation
from .signal_fusion import correlate
from .suspect_zones import suspect_zones


class ObservabilityAgent:
    def __init__(self, event_stream, ghost_test, service_map, model=None, *, window_seconds=300, max_revisions=1):
        self.stream = event_stream
        self.ghost = ghost_test
        self.service_map = service_map
        self.model = model
        self.window = timedelta(seconds=window_seconds)
        self.max_revisions = max_revisions
        self._lock = asyncio.Lock()
        self.reset()

    def reset(self):
        self.items = {}
        self.watermark = None
        self._last_eviction = None
        self.incident = None
        self.hypotheses = []
        self.feedback = None
        self.revisions = 0
        self.state = "collecting"
        self.errors = []
        self.duplicates = 0
        self.late = 0
        self.parsed = {}
        self._prepared = None
        self.input_version = 0
        self.generation = getattr(self, "generation", 0) + 1

    def observe(self, kind, data):
        item = observation(kind, data)
        if item is None:
            return
        if item.ts.tzinfo is None:
            raise ValueError("observation_timestamp_requires_timezone")
        key = (type(item).__name__, item.id)
        if key in self.items:
            if self.items[key] != item:
                raise ValueError("conflicting_observation_identity")
            self.duplicates += 1
            return
        if self.watermark and item.ts < self.watermark - self.window * 2:
            self.late += 1
            return
        self.watermark = max(self.watermark or item.ts, item.ts)
        self.items[key] = item
        self.input_version += 1
        if self.state in {"healthy", "insufficient_evidence", "hypotheses_ready", "hypotheses_ready_degraded",
                          "tested", "tested_degraded", "escalated", "degraded", "error"}:
            self._invalidate_analysis()
        if self._last_eviction is None or self.watermark - self._last_eviction >= timedelta(seconds=10):
            self.items = {k: v for k, v in self.items.items() if v.ts >= self.watermark - self.window * 2}
            self._last_eviction = self.watermark

    def snapshot(self):
        return {"state": self.state, "errors": self.errors, "duplicates": self.duplicates,
                "late": self.late, "revisions": self.revisions,
                "incident": self.incident.model_dump(mode="json") if self.incident else None,
                "hypotheses": [h.model_dump(mode="json") for h in self.hypotheses],
                "complaints": [c.model_dump(mode="json") for c in self.parsed.values()],
                "feedback": self.feedback.model_dump(mode="json") if self.feedback else None}

    async def run(self, *, until: datetime | None = None, handoff: bool = True):

        for _ in range(3):
            input_version = self.input_version
            result = await self._run_once(until=until, handoff=handoff)
            if input_version == self.input_version:
                return result
            self._invalidate_analysis()
        self.state = "collecting"
        self.errors = ["observation_batch_changed_during_investigation"]
        return self.snapshot()

    def _invalidate_analysis(self):
        self.incident = None
        self.hypotheses = []
        self.feedback = None
        self.parsed = {}
        self._prepared = None
        self.revisions = 0
        self.errors = []
        self.state = "collecting"
        self.generation += 1

    async def _run_once(self, *, until: datetime | None = None, handoff: bool = True):
        async with self._lock:
            if self.state in {"tested", "tested_degraded", "escalated", "degraded", "error"}:
                return self.snapshot()
            if self.state in {"hypotheses_ready", "hypotheses_ready_degraded"}:
                if not handoff:
                    return self.snapshot()
                return await self._complete(*self._prepared, handoff=True, reuse=True)
            cutoff = until or self.watermark
            if cutoff is None:
                return self.snapshot()
            if cutoff.tzinfo is None:
                raise ValueError("cutoff_requires_timezone")
            items = sorted((v for v in self.items.values() if cutoff - self.window * 2 <= v.ts <= cutoff), key=lambda v: (v.ts, v.id))
            events = [v for v in items if isinstance(v, Event)]
            boundary = cutoff - self.window
            current = [e for e in events if e.ts >= boundary]
            baseline = [e for e in events if boundary - self.window <= e.ts < boundary]


            failures = [e for e in current if e.code in {"ACK_TIMEOUT", "NO_CREDIT", "CREDIT_REJECTED"}]
            if not failures:
                self.state = "healthy"
                return self.snapshot()
            if not baseline:
                first_failure = min(e.ts for e in failures)
                baseline = [e for e in events if e.ts < first_failure and e.code == "ACK_RECEIVED"]
            groups = correlate(current)
            normal = correlate(baseline)
            normal_total = sum(g["total"] for g in normal)
            normal_failures = sum(g["failures"] for g in normal)
            if normal_total < 3:
                self.state = "insufficient_evidence"
                return self.snapshot()
            changed_groups = [g for g in groups if g["failures"] >= 3 and
                              g["failures"] / g["total"] > normal_failures / normal_total + 0.1]
            if not changed_groups:
                self.state = "healthy"
                return self.snapshot()
            zones = suspect_zones(current, self.service_map())
            evidence = {v.id for v in items}
            incident_key = hashlib.sha256('|'.join(e.id for e in failures).encode()).hexdigest()[:12]
            self.state = "investigating"
            self.incident = Incident(id=f"obs-{self.generation}-{incident_key}", status=IncidentStatus.INVESTIGATING,
                opened_at=min(e.ts for e in failures), signals=sorted(evidence), suspect_zones=sorted(zones),
                fingerprint={"groups": groups, "baseline": {"total": normal_total, "failures": normal_failures,
                    "scope": "available_activity_comparator_not_route_matched_normal"},
                    "window_end": cutoff.isoformat(), "window_seconds": self.window.total_seconds(),
                    "behaviours": behaviour_signals(current), "outcome": "unknown_without_explicit_observation"})
            await self.stream.ingest("incident", self.incident.model_dump(mode="json"))
            semaphore = asyncio.Semaphore(4)
            async def parse_one(complaint):
                try:
                    async with semaphore:
                        self.parsed[complaint.id] = await parse_complaint(complaint, events, self.model)
                except ModelUnavailable as exc:
                    self.errors.append(str(exc))
            await asyncio.gather(*(parse_one(v) for v in items if isinstance(v, Complaint) and v.id not in self.parsed))
            context = {"observations": [e.model_dump(mode="json") for e in events],
                       "customer_reports": [self.parsed.get(v.id, v).model_dump(mode="json") for v in items if isinstance(v, Complaint)],
                       "inferences": self.incident.fingerprint, "allowed_zones": zones,
                       "unknowns": ["unobserved payment outcomes", "root cause pending controlled tests"],
                       "service_map": self.service_map(), "feedback": None}

            representatives = {}
            for e in events:
                representatives.setdefault((e.service, e.route, e.code, e.payload.get("app_version")), e)
            context["observations"] = [e.model_dump(mode="json") for e in representatives.values()]
            context["inferences"] = {"baseline": self.incident.fingerprint["baseline"],
                "groups": [{k: v for k, v in g.items() if k != "transactions"} for g in groups],
                "behaviour_counts": dict(Counter(v["kind"] for v in self.incident.fingerprint["behaviours"]))}
            supplied = {e.id for e in representatives.values()} | {v.id for v in items if isinstance(v, Complaint)}
            context["evidence_ids"] = sorted(supplied)
            self._prepared = (context, supplied, zones)
            return await self._complete(context, supplied, zones, handoff=handoff)

    async def _complete(self, context, supplied, zones, *, handoff, reuse=False):
        for revision in range(self.max_revisions + 1):
            self.revisions = revision
            try:
                if not (reuse and revision == 0):
                    self.hypotheses = await investigate(self.model, context, supplied, zones)
            except ModelUnavailable as exc:
                self.state = "degraded"
                self.errors = sorted(set(self.errors + [str(exc)]))
                return self.snapshot()

            if not (reuse and revision == 0):
                self.hypotheses = [h.model_copy(update={"id": f"{self.incident.id}-r{revision}-{i}"})
                                   for i, h in enumerate(self.hypotheses, 1)]
            if not handoff:
                self.state = "hypotheses_ready_degraded" if self.errors else "hypotheses_ready"
                return self.snapshot()
            try:
                self.feedback = await self.ghost.evaluate(HypothesisSubmission(hypotheses=self.hypotheses))
                self.feedback = HypothesisEvaluationSet.model_validate(self.feedback.model_dump())
            except Exception:
                self.state = "error"
                self.errors.append("ghost_handoff_failed")
                return self.snapshot()
            submitted = {h.id for h in self.hypotheses}
            submitted_objects = {h.id: h for h in self.hypotheses}
            returned = [v.hypothesis.id for v in self.feedback.evaluations]
            if (len(returned) != len(submitted) or set(returned) != submitted
                or any(v.test_result.hypothesis_id != v.hypothesis.id for v in self.feedback.evaluations)
                or any(v.hypothesis != submitted_objects[v.hypothesis.id] for v in self.feedback.evaluations if v.hypothesis.id in submitted)
                or not set(self.feedback.validated_hypothesis_ids) <= submitted):
                self.state = "error"
                self.errors.append("ghost_feedback_identity_mismatch")
                return self.snapshot()
            if not self.feedback.send_back_to_observability:
                self.state = "tested_degraded" if self.errors else "tested"
                return self.snapshot()
            context["feedback"] = self.feedback.model_dump(mode="json")
            supplied.update(v.test_result.id for v in self.feedback.evaluations)
            context["evidence_ids"] = sorted(supplied)
            context["previous_candidates"] = [h.model_dump(mode="json") for h in self.hypotheses]
        self.state = "escalated"
        self.errors.append("evidence_insufficient_after_bounded_revisions")
        return self.snapshot()
