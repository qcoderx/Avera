"""HTTP surface for approvals, mounted with the host application's verified-principal dependency."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from avera.remediation.approvals.service import (
    ApprovalError,
    ApprovalNotFound,
    ApprovalNotPending,
    ApprovalService,
    Approver,
    ApproverNotAuthorised,
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
    current_approver: Callable[..., Approver],
    after_decision: Callable[[], Awaitable[None]] | None = None,
) -> APIRouter:
    """Build approval routes using an application-provided authenticated principal.

    The dependency must resolve a verified identity and role (for example from the app's
    signed session/JWT). Client-supplied role headers are deliberately not trusted here.
    """
    router = APIRouter(prefix="/approvals", tags=["approvals"])

    @router.get("")
    def list_approvals(
        incident_id: str | None = None,
        status: ApprovalStatus | None = None,
        approver: Approver = Depends(current_approver),
    ) -> list[dict]:
        return [
            a.model_dump(mode="json") for a in service.list(incident_id=incident_id, status=status)
            if a.required_role == approver.role
        ]

    @router.get("/{approval_id}")
    def get_approval(approval_id: str, approver: Approver = Depends(current_approver)) -> dict:
        try:
            approval = service.get(approval_id)
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")
        if approval.required_role != approver.role:
            raise HTTPException(404, "approval not found")
        return approval.model_dump(mode="json")

    @router.post("/{approval_id}")
    async def decide(
        approval_id: str,
        body: DecisionRequest,
        approver: Approver = Depends(current_approver),
    ) -> dict:
        try:
            decided = service.decide(
                approval_id,
                decision=body.decision,
                approver=approver,
                payload_hash=body.payload_hash,
                reason=body.reason,
            )
            if after_decision is not None:
                await after_decision()
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")
        except ApproverNotAuthorised as exc:
            raise HTTPException(403, str(exc))
        except StalePayload:
            raise HTTPException(412, "payload changed since it was shown; reload and review again")
        except ApprovalNotPending as exc:
            raise HTTPException(409, str(exc))
        except ApprovalError as exc:
            raise HTTPException(422, str(exc))
        return decided.model_dump(mode="json")

    return router
