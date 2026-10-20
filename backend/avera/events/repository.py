"""In-memory and PostgreSQL repositories for canonical stream records."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from avera.contracts import ContractObjectType, StreamRecord


class EventRepository(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def append(self, record: StreamRecord) -> tuple[StreamRecord, bool]: ...

    async def list_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]: ...


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
            return sequenced, True

    async def list_after(self, sequence: int, *, limit: int = 500) -> list[StreamRecord]:
        if sequence < 0 or limit < 1:
            raise ValueError("sequence must be non-negative and limit must be positive")
        async with self._lock:
            return [item for item in self._records if item.sequence > sequence][:limit]


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
                    return self._to_record(row), True
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


__all__ = ["EventRepository", "InMemoryEventRepository", "PostgresEventRepository"]
