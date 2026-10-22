import re
from decimal import Decimal
from datetime import timedelta

from avera.contracts import ParsedComplaint
from .model import structured_call
from .prompts import COMPLAINT


async def parse_complaint(complaint, events, model):
    def validate(parsed):
        text = complaint.raw_text.casefold()
        if parsed.amount is not None:
            numbers = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
            if str(parsed.amount.normalize()) not in {str(Decimal(n).normalize()) for n in numbers}:
                raise ValueError("unreported_amount")
        if parsed.product and parsed.product.casefold() not in text:
            raise ValueError("unreported_product")
        if parsed.time is not None and (parsed.time.tzinfo is None or parsed.time > complaint.ts or parsed.time.isoformat() not in complaint.raw_text):
            parsed.time = None
        if parsed.matched_txn:
            matches = [e for e in events if e.payload.get("transaction_id") == parsed.matched_txn
                       and abs(e.ts - complaint.ts) <= timedelta(hours=24)]
            owners = {e.customer_token for e in matches if e.customer_token is not None}
            if (parsed.matched_txn.casefold() not in text or not matches
                or complaint.customer_token is None or owners != {complaint.customer_token}):
                parsed.matched_txn = None
        return parsed
    parsed = await structured_call(model, COMPLAINT,
        {"customer_report": complaint.raw_text, "reported_at": complaint.ts.isoformat()}, ParsedComplaint, validate)
    return complaint.model_copy(update={"parsed": parsed})
