from __future__ import annotations

import hashlib
import json
from datetime import datetime, tzinfo
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

BANK_TZ = ZoneInfo("Africa/Lagos")


class Model(BaseModel):
    """Internal remediation models. Immutable; an unknown field is a bug."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def naira_to_kobo(amount: Decimal | str | int) -> int:
    kobo = Decimal(str(amount)) * 100
    if kobo != kobo.to_integral_value():
        raise ValueError(f"{amount} has fractions of a kobo")
    return int(kobo)


def kobo_to_naira(kobo: int) -> Decimal:
    return Decimal(kobo) / 100


def format_naira(kobo: int) -> str:
    """'N' rather than the naira sign: the naira sign is not GSM-7 and would force UCS-2 SMS encoding."""
    naira, rest = divmod(kobo, 100)
    return f"N{naira:,}" if rest == 0 else f"N{naira:,}.{rest:02d}"


def format_bank_time(ts: datetime, tz: tzinfo = BANK_TZ) -> str:
    local = ts.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
