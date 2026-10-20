"""Apology SMS drafting. The dispute team approves the exact messages that will be sent, not a template."""

from __future__ import annotations

import math
import re
from datetime import datetime, tzinfo

from avera.remediation.common import BANK_TZ, format_bank_time, format_naira
from avera.remediation.models import BlastRadius, Estimate, SmsDraft, SmsMessage

TEMPLATE = (
    "We're sorry. We found an issue affecting your {impact}. You do not need to contact us. "
    "We are fixing it now and {eta_phrase}. Ref: {ref}."
)
FALLBACK_ETA_PHRASE = "will update you as soon as it is resolved"

MAX_SEGMENTS = 2
GSM7_SEGMENT_CHARS = 153
_GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ!\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_FORBIDDEN = (
    (re.compile(r"https?://|www\.", re.I), "links"),
    (re.compile(r"\b\d{10}\b"), "account numbers"),
    (re.compile(r"\b(pin|otp|password|cvv)\b", re.I), "credential requests"),
)


class SmsPolicyViolation(ValueError):
    pass


def eta_phrase(eta_full: Estimate) -> tuple[str, bool]:
    if eta_full.confidence != "high" or eta_full.minutes == 0:
        return FALLBACK_ETA_PHRASE, True
    if eta_full.minutes < 60:
        return f"expect it to be resolved within {eta_full.minutes} minutes", False
    hours = math.ceil(eta_full.minutes / 60)
    return f"expect it to be resolved within {hours} hour{'s' if hours > 1 else ''}", False


def check_sms_policy(text: str) -> None:
    if not set(text) <= _GSM7:
        raise SmsPolicyViolation("non GSM-7 characters force UCS-2 encoding (70 chars per segment)")
    if len(text) > MAX_SEGMENTS * GSM7_SEGMENT_CHARS:
        raise SmsPolicyViolation(f"message exceeds {MAX_SEGMENTS} SMS segments")
    for pattern, what in _FORBIDDEN:
        if pattern.search(text):
            raise SmsPolicyViolation(f"message must not contain {what}")


def draft_sms(
    blast_radius: BlastRadius,
    eta_full: Estimate,
    txn_details: dict[str, tuple[int, datetime]],
    *,
    reference: str,
    tz: tzinfo = BANK_TZ,
) -> SmsDraft:
    """txn_details maps each affected txn_id to (amount_kobo, ts)."""
    phrase, fallback = eta_phrase(eta_full)
    messages = []
    for customer in blast_radius.customers:
        if len(customer.txn_ids) == 1:
            amount, ts = txn_details[customer.txn_ids[0]]
            impact = f"{format_naira(amount)} transfer at {format_bank_time(ts, tz)}"
        else:
            impact = (
                f"{len(customer.txn_ids)} transfers totalling {format_naira(customer.exposure_kobo)} "
                f"since {format_bank_time(customer.first_affected_at, tz)}"
            )
        text = TEMPLATE.format(impact=impact, eta_phrase=phrase, ref=reference)
        check_sms_policy(text)
        messages.append(SmsMessage(customer_token=customer.customer_token, text=text))

    return SmsDraft(
        incident_id=blast_radius.incident_id,
        template=TEMPLATE,
        eta_phrase=phrase,
        eta_confidence=eta_full.confidence,
        fallback_used=fallback,
        recipient_count=len(messages),
        messages=messages,
    )
