from __future__ import annotations

import logging
from dataclasses import dataclass

from avera.remediation.models import SmsDraft
from avera.remediation.ports import ContactDirectory, SmsGateway

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsDispatchReport:
    sent: int
    skipped_opted_out: int
    skipped_unreachable: int
    failed: int
    failed_customer_tokens: tuple[str, ...] = ()


def dispatch_sms(
    draft: SmsDraft,
    approval_id: str,
    contacts: ContactDirectory,
    gateway: SmsGateway,
    *,
    customer_tokens: set[str] | None = None,
) -> SmsDispatchReport:
    """Send exactly the approved messages. Phone numbers are resolved per message and never stored or logged."""
    sent = opted_out = unreachable = failed = 0
    failed_tokens: list[str] = []
    for message in draft.messages:
        if customer_tokens is not None and message.customer_token not in customer_tokens:
            continue
        contact = contacts.resolve(message.customer_token)
        if contact is None:
            unreachable += 1
            continue
        if not contact.sms_opt_in:
            opted_out += 1
            continue
        try:
            gateway.send(contact.phone_e164, message.text, idempotency_key=f"{approval_id}:{message.customer_token}")
            sent += 1
        except Exception:
            log.exception("sms send failed for an eligible recipient")
            failed += 1
            failed_tokens.append(message.customer_token)
    return SmsDispatchReport(sent=sent, skipped_opted_out=opted_out, skipped_unreachable=unreachable,
                             failed=failed, failed_customer_tokens=tuple(failed_tokens))
