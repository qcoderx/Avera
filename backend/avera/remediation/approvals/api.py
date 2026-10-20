"""HTTP surface for approvals. Koded mounts this router in the main FastAPI app.

Identity comes from X-Avera-User / X-Avera-Role headers until real authentication exists.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
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
from avera.remediation.models import ApprovalStatus, Role


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    payload_hash: str
    reason: str | None = None


def build_router(service: ApprovalService) -> APIRouter:
    router = APIRouter(prefix="/approvals", tags=["approvals"])

    @router.get("")
    def list_approvals(incident_id: str | None = None, status: ApprovalStatus | None = None) -> list[dict]:
        return [a.model_dump(mode="json") for a in service.list(incident_id=incident_id, status=status)]

    @router.get("/{approval_id}")
    def get_approval(approval_id: str) -> dict:
        try:
            return service.get(approval_id).model_dump(mode="json")
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")

    @router.post("/{approval_id}")
    def decide(
        approval_id: str,
        body: DecisionRequest,
        x_avera_user: str = Header(...),
        x_avera_role: Role = Header(...),
    ) -> dict:
        try:
            decided = service.decide(
                approval_id,
                decision=body.decision,
                approver=Approver(id=x_avera_user, role=x_avera_role),
                payload_hash=body.payload_hash,
                reason=body.reason,
            )
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
