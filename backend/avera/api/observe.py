"""Safe, process-local operational logs and the ``/observe`` console."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse


_REDACTIONS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"), "<redacted-api-key>"),
    (
        re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@"),
        r"\1<redacted>@",
    ),
    (
        re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?<![\d-])\+?\d{10,15}(?![\d-])"), "<redacted-phone>"),
)


def sanitize_log_message(message: object, *, limit: int = 700) -> str:
    """Remove credential-like values before a message reaches the public console."""

    cleaned = str(message).replace("\r", " ").replace("\n", " ")
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        return f"{cleaned[: limit - 1]}…"
    return cleaned


@dataclass(frozen=True, slots=True)
class OperationalLog:
    id: int
    timestamp: str
    level: str
    logger: str
    message: str
    kind: str
    context: dict[str, object]


class OperationalLogStore:
    """Bounded, thread-safe log history for the current API process."""

    _context_fields = {"method", "path", "status", "duration_ms", "request_id"}

    def __init__(self, *, capacity: int = 1_000) -> None:
        self._records: deque[OperationalLog] = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._next_id = 1
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = monotonic()

    def append(
        self,
        level: str,
        logger_name: str,
        message: object,
        *,
        kind: str = "application",
        **context: object,
    ) -> OperationalLog:
        safe_context = {
            key: sanitize_log_message(value) if isinstance(value, str) else value
            for key, value in context.items()
            if key in self._context_fields and value is not None
        }
        with self._lock:
            record = OperationalLog(
                id=self._next_id,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                level=level.strip().upper(),
                logger=sanitize_log_message(logger_name, limit=120),
                message=sanitize_log_message(message),
                kind=sanitize_log_message(kind, limit=40),
                context=safe_context,
            )
            self._next_id += 1
            self._records.append(record)
            return record

    def records_after(
        self,
        after: int = 0,
        *,
        limit: int = 200,
        level: str | None = None,
        query: str | None = None,
    ) -> list[OperationalLog]:
        expected_level = level.strip().upper() if level else None
        needle = query.strip().casefold() if query else None
        with self._lock:
            records = list(self._records)
        matches = []
        for record in records:
            if record.id <= after:
                continue
            if expected_level and record.level != expected_level:
                continue
            if needle and needle not in (
                f"{record.logger} {record.message} {record.context}".casefold()
            ):
                continue
            matches.append(record)
        return matches[-limit:]

    def stats(self) -> dict[str, object]:
        with self._lock:
            records = list(self._records)
            next_id = self._next_id
        return {
            "retained": len(records),
            "capacity": self._records.maxlen,
            "latest_id": next_id - 1,
            "levels": dict(Counter(record.level for record in records)),
            "started_at": self.started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "uptime_seconds": round(monotonic() - self.started_monotonic),
        }


class OperationalLogHandler(logging.Handler):
    """Capture log messages without exposing exceptions, arguments, or stack traces."""

    def __init__(self, store: OperationalLogStore) -> None:
        super().__init__(level=logging.INFO)
        self.store = store
        self.previous_avera_level: int | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.store.append(
                record.levelname,
                record.name,
                record.getMessage(),
                kind="application",
            )
        except Exception:
            self.handleError(record)


def install_log_capture(handler: OperationalLogHandler) -> None:
    root = logging.getLogger()
    root.addHandler(handler)
    avera_logger = logging.getLogger("avera")
    handler.previous_avera_level = avera_logger.level
    avera_logger.setLevel(logging.INFO)


def remove_log_capture(handler: OperationalLogHandler) -> None:
    logging.getLogger().removeHandler(handler)
    if handler.previous_avera_level is not None:
        logging.getLogger("avera").setLevel(handler.previous_avera_level)
        handler.previous_avera_level = None


def _record_payload(record: OperationalLog) -> dict[str, object]:
    return asdict(record)


def build_observe_router(
    store: OperationalLogStore,
    status_provider: Callable[[], Awaitable[dict[str, object]]],
) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    ui_dir = Path(__file__).with_name("observe_ui")
    security_headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-Robots-Tag": "noindex, nofollow",
    }

    def ui_file(name: str, media_type: str) -> FileResponse:
        path = ui_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="observe asset not found")
        return FileResponse(path, media_type=media_type, headers=security_headers)

    @router.get("/observe")
    @router.get("/observe/")
    async def observe_page() -> FileResponse:
        return ui_file("index.html", "text/html")

    @router.get("/observe/styles.css")
    async def observe_styles() -> FileResponse:
        return ui_file("styles.css", "text/css")

    @router.get("/observe/app.js")
    async def observe_script() -> FileResponse:
        return ui_file("app.js", "text/javascript")

    @router.get("/observe/api/status")
    async def observe_status() -> JSONResponse:
        payload = await status_provider()
        payload["logs"] = store.stats()
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/observe/api/logs")
    async def observe_logs(
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
        level: str | None = Query(default=None, max_length=16),
        q: str | None = Query(default=None, max_length=120),
    ) -> JSONResponse:
        if level and level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise HTTPException(status_code=422, detail="unsupported log level")
        records = store.records_after(after, limit=limit, level=level, query=q)
        return JSONResponse(
            {
                "records": [_record_payload(record) for record in records],
                "latest_id": store.stats()["latest_id"],
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/observe/api/stream")
    async def observe_stream(
        request: Request,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        cursor = after

        async def generate():
            nonlocal cursor
            idle_ticks = 0
            while not await request.is_disconnected():
                records = store.records_after(cursor, limit=200)
                if records:
                    idle_ticks = 0
                    for record in records:
                        cursor = record.id
                        data = json.dumps(_record_payload(record), separators=(",", ":"))
                        yield f"id: {record.id}\nevent: log\ndata: {data}\n\n"
                else:
                    idle_ticks += 1
                    if idle_ticks >= 15:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


__all__ = [
    "OperationalLogHandler",
    "OperationalLogStore",
    "build_observe_router",
    "install_log_capture",
    "remove_log_capture",
    "sanitize_log_message",
]
