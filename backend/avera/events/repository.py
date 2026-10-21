"""In-memory and PostgreSQL repositories for canonical stream records."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from avera.contracts import ContractObjectType, StreamRecord


class EventRepository(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def append(self, record: StreamRecord) -> tuple[StreamRecord, bool]: ...

    async def list_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]: ...

    async def claim_outbox(self, *, limit: int = 100) -> list["OutboxItem"]: ...

    async def finish_outbox(
        self, sequence: int, *, dead_lettered: bool = False, error: str | None = None
    ) -> None: ...

    async def outbox_pending_count(self) -> int: ...

    async def outbox_status(self, sequence: int) -> str | None: ...


@dataclass(frozen=True, slots=True)
class OutboxItem:
    record: StreamRecord
    attempts: int


def _record_key(record: StreamRecord) -> tuple[str, str, datetime]:
    object_id = record.data.get("id")
    if not isinstance(object_id, str) or not object_id:
        raise ValueError(f"{record.type.value} payload must include a stable id")
    # One domain object (notably an Incident) may emit multiple lifecycle records.
    # The event timestamp distinguishes those updates while making fixture replay idempotent.
    return record.type.value, object_id, record.occurred_at


class InMemoryEventRepository:
    """Deterministic local store; sequence assignment and dedupe are atomic."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: list[StreamRecord] = []
        self._by_key: dict[tuple[str, str, datetime], StreamRecord] = {}
        self._outbox: dict[int, dict[str, object]] = {}

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def append(self, record: StreamRecord) -> tuple[StreamRecord, bool]:
        key = _record_key(record)
        async with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                return existing, False
            sequenced = record.model_copy(update={"sequence": len(self._records) + 1})
            self._records.append(sequenced)
            self._by_key[key] = sequenced
            self._outbox[sequenced.sequence] = {
                "record": sequenced, "attempts": 0, "status": "pending"
            }
            return sequenced, True

    async def list_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]:
        if sequence < 0 or limit < 1:
            raise ValueError("sequence must be non-negative and limit must be positive")
        async with self._lock:
            return [item for item in self._records if item.sequence > sequence][:limit]

    async def claim_outbox(self, *, limit: int = 100) -> list[OutboxItem]:
        if limit < 1:
            raise ValueError("limit must be positive")
        async with self._lock:
            items = []
            for sequence in sorted(self._outbox):
                entry = self._outbox[sequence]
                if entry["status"] != "pending":
                    continue
                entry["attempts"] = int(entry["attempts"]) + 1
                items.append(OutboxItem(entry["record"], int(entry["attempts"])))
                if len(items) == limit:
                    break
            return items

    async def finish_outbox(
        self, sequence: int, *, dead_lettered: bool = False, error: str | None = None
    ) -> None:
        async with self._lock:
            entry = self._outbox.get(sequence)
            if entry is None:
                raise KeyError(f"outbox sequence {sequence} not found")
            if error is not None and not dead_lettered:
                return
            entry["status"] = "dead_lettered" if dead_lettered else "published"

    async def outbox_pending_count(self) -> int:
        async with self._lock:
            return sum(entry["status"] == "pending" for entry in self._outbox.values())

    async def outbox_status(self, sequence: int) -> str | None:
        async with self._lock:
            entry = self._outbox.get(sequence)
            return str(entry["status"]) if entry is not None else None


class PostgresEventRepository:
    """PostgreSQL-backed append-only event log with idempotent object ingestion."""

    def __init__(self, dsn: str, *, min_pool_size: int = 1, max_pool_size: int = 10) -> None:
        self.dsn = dsn
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self._pool = None

    async def start(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - exercised in configured deployments
            raise RuntimeError("Install asyncpg to enable PostgreSQL persistence") from exc

        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
        )
        schema_path = Path(__file__).resolve().parents[2] / "schema.sql"
        async with self._pool.acquire() as connection:
            await connection.execute(schema_path.read_text(encoding="utf-8"))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def append(self, record: StreamRecord) -> tuple[StreamRecord, bool]:
        pool = self._require_pool()
        object_type, object_id, occurred_at = _record_key(record)
        async with pool.acquire() as connection:
            async with connection.transaction():
                # Serialize sequence allocation and commit order for this single-bank stream.
                await connection.execute("SELECT pg_advisory_xact_lock(735251, 1)")
                row = await connection.fetchrow(
                    """
                    INSERT INTO avera_stream_records (object_type, object_id, occurred_at, data)
                    VALUES ($1, $2, $3, $4::jsonb)
                    ON CONFLICT (object_type, object_id, occurred_at) DO NOTHING
                    RETURNING sequence, object_type, occurred_at, data
                    """,
                    object_type,
                    object_id,
                    occurred_at,
                    json.dumps(record.data, separators=(",", ":")),
                )
                if row is not None:
                    stored = self._to_record(row)
                    await connection.execute(
                        "INSERT INTO avera_event_outbox (sequence) VALUES ($1)", stored.sequence
                    )
                    return stored, True
                existing = await connection.fetchrow(
                    """
                    SELECT sequence, object_type, occurred_at, data
                    FROM avera_stream_records
                    WHERE object_type = $1 AND object_id = $2 AND occurred_at = $3
                    """,
                    object_type,
                    object_id,
                    occurred_at,
                )
                if existing is None:  # Defensive against an unexpected concurrent delete.
                    raise RuntimeError("idempotent event row disappeared during append")
                return self._to_record(existing), False

    async def list_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]:
        if sequence < 0 or limit < 1:
            raise ValueError("sequence must be non-negative and limit must be positive")
        pool = self._require_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT sequence, object_type, occurred_at, data
                FROM avera_stream_records
                WHERE sequence > $1
                ORDER BY sequence ASC
                LIMIT $2
                """,
                sequence,
                limit,
            )
        return [self._to_record(row) for row in rows]

    async def claim_outbox(self, *, limit: int = 100) -> list[OutboxItem]:
        if limit < 1:
            raise ValueError("limit must be positive")
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    WITH claimable AS (
                        SELECT sequence
                        FROM avera_event_outbox
                        WHERE delivered_at IS NULL
                          AND dead_lettered_at IS NULL
                          AND (claimed_until IS NULL OR claimed_until < now())
                          AND next_attempt_at <= now()
                        ORDER BY sequence
                        LIMIT $1
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE avera_event_outbox AS outbox
                    SET claimed_until = now() + interval '2 minutes',
                        attempts = outbox.attempts + 1
                    FROM claimable, avera_stream_records AS event
                    WHERE outbox.sequence = claimable.sequence
                      AND event.sequence = outbox.sequence
                    RETURNING event.sequence, event.object_type, event.occurred_at, event.data,
                              outbox.attempts
                    """,
                    limit,
                )
        return [
            OutboxItem(self._to_record(row), row["attempts"])
            for row in rows
        ]

    async def finish_outbox(
        self, sequence: int, *, dead_lettered: bool = False, error: str | None = None
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            if error is not None and not dead_lettered:
                await connection.execute(
                    """UPDATE avera_event_outbox
                       SET claimed_until = NULL, last_error = $2,
                           next_attempt_at = now() +
                               (LEAST(power(2, attempts), 300) * interval '1 second')
                       WHERE sequence = $1""",
                    sequence,
                    error,
                )
                return
            status_column = "dead_lettered_at" if dead_lettered else "delivered_at"
            await connection.execute(
                f"""UPDATE avera_event_outbox
                    SET {status_column} = now(), claimed_until = NULL, last_error = $2
                    WHERE sequence = $1""",
                sequence,
                error,
            )

    async def outbox_pending_count(self) -> int:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            return await connection.fetchval(
                """SELECT count(*) FROM avera_event_outbox
                   WHERE delivered_at IS NULL AND dead_lettered_at IS NULL"""
            )

    async def outbox_status(self, sequence: int) -> str | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            state = await connection.fetchval(
                """SELECT CASE
                         WHEN delivered_at IS NOT NULL THEN 'published'
                         WHEN dead_lettered_at IS NOT NULL THEN 'dead_lettered'
                         ELSE 'pending'
                       END
                   FROM avera_event_outbox WHERE sequence = $1""",
                sequence,
            )
        return state

    def _require_pool(self):
        if self._pool is None:
            raise RuntimeError("PostgreSQL repository has not been started")
        return self._pool

    @staticmethod
    def _to_record(row) -> StreamRecord:
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        return StreamRecord(
            sequence=row["sequence"],
            type=ContractObjectType(row["object_type"]),
            occurred_at=row["occurred_at"],
            data=data,
        )


__all__ = ["EventRepository", "InMemoryEventRepository", "OutboxItem", "PostgresEventRepository"]
