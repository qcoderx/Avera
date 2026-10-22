"""HTTP surface for the contained mock-bank repair demo."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from avera.bank_Demo.service import BankDemoRuntime


class TransferRequest(BaseModel):
    amount_ngn: int = Field(default=25_000, ge=100, le=1_000_000)


class IssueReportRequest(BaseModel):
    complaint: str = Field(min_length=10, max_length=500)


def build_bank_demo_router(runtime: BankDemoRuntime) -> APIRouter:
    router = APIRouter(prefix="/bank-demo", tags=["bank demo"])

    @router.get("/state")
    async def state() -> dict[str, object]:
        return await runtime.state()

    @router.post("/trigger")
    async def trigger() -> dict[str, object]:
        return await runtime.trigger()

    @router.post("/transfer")
    async def transfer(request: TransferRequest) -> dict[str, object]:
        try:
            return await runtime.transfer(request.amount_ngn)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/report")
    async def report(request: IssueReportRequest) -> dict[str, object]:
        try:
            return await runtime.report_issue(request.complaint)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/repair")
    async def repair() -> dict[str, object]:
        try:
            return await runtime.start_repair()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/reset")
    async def reset() -> dict[str, object]:
        return await runtime.reset()

    return router


__all__ = ["build_bank_demo_router"]
