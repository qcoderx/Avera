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


def dispatch_sms(draft: SmsDraft, approval_id: str, contacts: ContactDirectory, gateway: SmsGateway) -> SmsDispatchReport:
    """Send exactly the approved messages. Phone numbers are resolved per message and never stored or logged."""
    sent = opted_out = unreachable = failed = 0
    for message in draft.messages:
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
            log.exception("sms send failed for customer %s", message.customer_token)
            failed += 1
    return SmsDispatchReport(sent=sent, skipped_opted_out=opted_out, skipped_unreachable=unreachable, failed=failed)
