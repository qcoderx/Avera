"""Public HTTP surface for simulated-MVP approval decisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from avera.remediation.approvals.service import (
    ApprovalError,
    ApprovalNotFound,
    ApprovalNotPending,
    ApprovalService,
    Approver,
    StalePayload,
)
from avera.remediation.models import ApprovalStatus


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    payload_hash: str
    reason: str | None = None


def build_router(
    service: ApprovalService,
    *,
    after_decision: Callable[[], Awaitable[None]] | None = None,
) -> APIRouter:
    """Build open demo routes while deriving decision authority on the server.

    This MVP is public and only controls the simulated bank. The server applies the
    approval's required role itself; client-supplied identity and role values are ignored.
    """
    router = APIRouter(prefix="/approvals", tags=["approvals"])

    @router.get("")
    def list_approvals(
        incident_id: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[dict]:
        return [a.model_dump(mode="json") for a in service.list(incident_id=incident_id, status=status)]

    @router.get("/{approval_id}")
    def get_approval(approval_id: str) -> dict:
        try:
            approval = service.get(approval_id)
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")
        return approval.model_dump(mode="json")

    @router.post("/{approval_id}")
    async def decide(
        approval_id: str,
        body: DecisionRequest,
    ) -> dict:
        try:
            approval = service.get(approval_id)
            decided = service.decide(
                approval_id,
                decision=body.decision,
                approver=Approver(id="demo-platform-operator", role=approval.required_role),
                payload_hash=body.payload_hash,
                reason=body.reason,
            )
            if after_decision is not None:
                await after_decision()
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")
        except StalePayload:
            raise HTTPException(412, "payload changed since it was shown; reload and review again")
        except ApprovalNotPending as exc:
            raise HTTPException(409, str(exc))
        except ApprovalError as exc:
            raise HTTPException(422, str(exc))
        return decided.model_dump(mode="json")

    return router
