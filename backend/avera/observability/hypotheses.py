"""Validate language candidates against real evidence and allowed resolver ownership."""
from pydantic import Field
from avera.contracts import ContractModel, Hypothesis
from .model import structured_call
from .prompts import INVESTIGATOR
from .ranking import rank


class CandidateValidationError(ValueError):
    pass


class Candidates(ContractModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=10)


async def investigate(model, context, evidence, zones):
    def validate(result):
        ids = set()
        signatures = set()
        observations = {v["id"]: v for v in context["observations"]}
        for h in result.hypotheses:
            signature = (h.zone, h.claim.casefold())
            if h.id in ids or signature in signatures:
                raise CandidateValidationError("duplicate_hypothesis")
            ids.add(h.id)
            signatures.add(signature)
            if h.zone not in zones or not h.routed_to or set(h.routed_to) != set(zones[h.zone]["routed_to"]):
                raise CandidateValidationError("invalid_component_or_resolver")
            if not h.evidence_for or not set(h.evidence_for + h.evidence_against) <= evidence:
                raise CandidateValidationError("invalid_evidence_reference")
            component = zones[h.zone]["component"]
            direct = {ref for ref, item in observations.items() if item["service"] == component}
            if h.zone == "mobile_app.release":
                direct = {ref for ref, item in observations.items() if item["code"] in {"APP_RELEASED", "DEPLOY_SUCCEEDED"}}
            if h.zone.startswith("partner_bank."):
                direct.update(ref for ref, item in observations.items()
                    if "destination-bank-" + str(item.get("payload", {}).get("destination_bank", "")).lower() == component)
            if not set(h.evidence_for) & direct:
                raise CandidateValidationError("missing_component_support")
            if set(h.evidence_for) & set(h.evidence_against):
                raise CandidateValidationError("contradictory_reference_assignment")
            if len(h.claim) < 40 or len(h.disproof) < 30:
                raise CandidateValidationError("insufficient_mechanism_or_falsification")
        relevant_release = any(v.get("code") in {"APP_RELEASED", "DEPLOY_SUCCEEDED"} for v in context["observations"])
        if relevant_release and "mobile_app.release" in zones and not any(h.zone == "mobile_app.release" for h in result.hypotheses):
            raise CandidateValidationError("missing_release_candidate")
        if len(zones) >= 3 and len({h.zone for h in result.hypotheses}) < 3:
            raise CandidateValidationError("insufficient_competing_candidates")
        rejected = {(v["hypothesis"]["zone"], v["hypothesis"]["claim"].casefold())
                    for v in (context.get("feedback") or {}).get("evaluations", [])
                    if v["test_result"]["verdict"] == "rejected"}
        if signatures & rejected:
            raise CandidateValidationError("unchanged_rejected_candidate")
        return rank(result.hypotheses)
    return await structured_call(model, INVESTIGATOR, context, Candidates, validate)
