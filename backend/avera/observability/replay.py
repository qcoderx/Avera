"""An allowlist boundary shared by live ingestion and chronological fixture replay."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from avera.contracts import Complaint, Event

PAYLOAD_FIELDS = {"transaction_id", "idempotency_key", "destination_bank", "amount_ngn",
                  "app_version", "version", "change_id", "customer_visible_status",
                  "destination_credit_posted", "gateway"}
EVENT_CODES = {"TRANSFER_REQUESTED", "DEBIT_POSTED", "INSUFFICIENT_FUNDS", "CREDIT_ACCEPTED",
               "CREDIT_REJECTED", "ACK_RECEIVED", "ACK_TIMEOUT", "NO_CREDIT", "IDEMPOTENT_RETRY",
               "HISTORY_REFRESH", "CHANNEL_SWITCH", "ABANDONED", "APP_RELEASED",
               "DEPLOY_SUCCEEDED", "RETRY_RATE_SPIKE", "CHANNEL_SWITCH_SPIKE"}


def observation(kind, data):
    if str(kind) == "complaint":
        # Never trust fixture pre-parsing as language interpretation.
        return Complaint.model_validate({**data, "parsed": None})
    if str(kind) != "event" or data.get("code") not in EVENT_CODES:
        return None
    if data.get("source") in {"demo-failure-injector", "ghost-test", "admin-api"}:
        return None
    return Event.model_validate({**data, "payload": {
        k: v for k, v in data.get("payload", {}).items() if k in PAYLOAD_FIELDS
    }})


def replay_observations(path: Path, until: datetime):
    """Do not deserialize fixture contracts/labels/results into the investigator."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    selected = []
    for record in raw["records"]:
        if record["type"] not in {"event", "complaint"}:
            continue
        ts = datetime.fromisoformat(record["data"]["ts"].replace("Z", "+00:00"))
        if ts > until:
            continue
        item = observation(record["type"], record["data"])
        if item is not None:
            selected.append(item)
    return sorted(selected, key=lambda item: (item.ts, item.id))
