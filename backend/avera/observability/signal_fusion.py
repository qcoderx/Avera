from collections import defaultdict

FAILURES = {"ACK_TIMEOUT", "NO_CREDIT", "CREDIT_REJECTED"}
TERMINALS = FAILURES | {"ACK_RECEIVED"}


def correlate(events):
    transactions = defaultdict(list)
    for event in sorted(events, key=lambda e: (e.ts, e.id)):
        txn = event.payload.get("transaction_id")
        if txn:
            transactions[txn].append(event)
    groups = defaultdict(list)
    for txn, items in transactions.items():
        if len({e.customer_token for e in items if e.customer_token is not None}) > 1:
            continue
        requests = [e for e in items if e.code == "TRANSFER_REQUESTED"]
        terminals = [e for e in items if e.code in TERMINALS]
        if not terminals:
            continue
        terminal = terminals[-1]
        request = requests[0] if requests else None
        key = (terminal.route, terminal.service,
               terminal.payload.get("destination_bank") or (request.payload.get("destination_bank") if request else None),
               request.payload.get("app_version") if request else None)
        groups[key].append({"transaction_id": txn, "ts": terminal.ts,
                            "failed": terminal.code in FAILURES,
                            "evidence": [e.id for e in items],
                            "error_sequence": [e.code for e in items]})
    return [{"route": k[0], "gateway": k[1], "destination": k[2], "app_version": k[3],
             "transactions": v, "failures": sum(x["failed"] for x in v), "total": len(v)}
            for k, v in sorted(groups.items(), key=lambda x: str(x[0]))]
