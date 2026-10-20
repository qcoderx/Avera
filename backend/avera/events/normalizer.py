"""Validation and normalization at external event-ingestion boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from avera.contracts import ContractObjectType, StreamRecord


def normalize_record(
    object_type: ContractObjectType | str,
    payload: dict[str, Any],
    *,
    occurred_at: datetime | None = None,
    source: str | None = None,
) -> StreamRecord:
    """Return a validated, canonical stream envelope with a placeholder sequence.

    Sequence numbers are assigned only by the repository, never trusted from an
    integration caller. ``source`` labels otherwise-unlabelled Event webhooks.
    """
    kind = ContractObjectType(object_type)
    data = dict(payload)
    if kind == ContractObjectType.EVENT and source and not data.get("source"):
        data["source"] = source

    typed_model = StreamRecord._payload_models[kind]
    typed_payload = typed_model.model_validate(data)
    canonical_data = typed_payload.model_dump(mode="json")
    if occurred_at is None:
        occurred_at = _payload_timestamp(typed_payload) or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must include an explicit UTC offset")

    record = StreamRecord(
        sequence=1,
        type=kind,
        occurred_at=occurred_at,
        data=canonical_data,
    )
    # Keep the registered contract validation at the envelope boundary too.
    record.validate_payload()
    return record


def _payload_timestamp(payload: Any) -> datetime | None:
    for name in ("ts", "opened_at", "time", "decided_at"):
        value = getattr(payload, name, None)
        if isinstance(value, datetime):
            return value
    return None


__all__ = ["normalize_record"]
