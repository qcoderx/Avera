from __future__ import annotations

import asyncio
import json
import os
from typing import Protocol

from pydantic import BaseModel


class LanguageModel(Protocol):
    async def generate(self, instructions: str, context: dict, schema: type[BaseModel]) -> BaseModel: ...


class ModelUnavailable(RuntimeError):
    pass


class OpenAIObservabilityModel:
    source = "openai"

    def __init__(self, model: str, *, api_key: str, base_url: str | None = None):
        from openai import AsyncOpenAI
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)

    async def generate(self, instructions, context, schema):
        response = await self.client.responses.parse(
            model=self.model, instructions=instructions,
            input=json.dumps(context, default=str), text_format=schema,
            max_output_tokens=3000, store=False,
        )
        if response.output_parsed is None:
            raise ModelUnavailable("model_empty_or_refused")
        return response.output_parsed

    async def close(self):
        await self.client.close()


def configured_model_name():
    return os.getenv("AVERA_OBSERVABILITY_MODEL") or os.getenv("AVERA_GHOST_SUMMARY_MODEL")


def configured_base_url():

    return os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"


def configured_model():
    model = configured_model_name()
    key = os.getenv("OPENAI_API_KEY")
    if not model or not key:
        return None
    return OpenAIObservabilityModel(model, api_key=key, base_url=configured_base_url())


async def structured_call(model, instructions, context, schema, validate=lambda value: value):
    if model is None:
        raise ModelUnavailable("model_not_configured")
    request_context = dict(context)
    request_instructions = instructions
    for attempt in range(2):
        phase = "request"
        try:
            value = await asyncio.wait_for(model.generate(request_instructions, request_context, schema), timeout=35)
            phase = "validation"
            value = schema.model_validate(value.model_dump() if isinstance(value, BaseModel) else value)
            return validate(value)
        except Exception as exc:
            if type(exc).__name__ == "CandidateValidationError":
                request_context["previous_output_error"] = str(exc)
                request_context["correction"] = "Use only exact strings from evidence_ids as references; no transaction IDs, prefixes or invented labels. Each candidate needs at least one supporting reference. Correct the previous validation error."
                if str(exc) == "insufficient_competing_candidates":
                    request_context["correction"] = "Return at least three candidates spanning at least three distinct allowed_zones with real supporting references. Include the release as one candidate when observed. Duplicate-zone candidates do not count as distinct zones. State uncertainty and contrary observations for weaker candidates; do not invent evidence."
                elif str(exc) == "invalid_component_or_resolver":
                    request_context["correction"] = "For each zone, routed_to must exactly match that zone's routed_to list in allowed_zones. Do not borrow another component's resolver tags."
                elif str(exc) == "missing_component_support":
                    request_context["correction"] = "Each candidate's evidence_for needs a supplied observation of its component. Release candidates need a release/deployment reference; partner candidates can cite observations explicitly naming their destination."
                elif str(exc) == "contradictory_reference_assignment":
                    request_context["correction"] = (
                        "For every hypothesis, evidence_for and evidence_against must be disjoint sets. "
                        "Do not repeat any evidence ID across those two lists. Keep a supporting ID only "
                        "in evidence_for; cite a different supplied observation in evidence_against only "
                        "if it genuinely contradicts the claim. Otherwise return evidence_against as []."
                    )
                if type(exc).__name__ == "CandidateValidationError":

                    request_instructions = (
                        f"{instructions}\n\nVALIDATION RETRY — correct the previous output.\n"
                        f"Validation code: {exc}. {request_context['correction']} "
                        "Return a complete corrected object that satisfies this correction."
                    )
            if attempt:

                status = getattr(exc, "status_code", None)
                suffix = f"_http_{status}" if isinstance(status, int) else ""
                if type(exc).__name__ == "CandidateValidationError":
                    suffix = ":" + str(exc)
                raise ModelUnavailable(f"model_call_or_validation_failed:{phase}:{type(exc).__name__}{suffix}") from None
