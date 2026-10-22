from collections import defaultdict


def behaviour_signals(events):
    groups = defaultdict(list)
    for event in events:
        if event.customer_token:
            groups[event.customer_token].append(event)
    result = []
    for customer, items in sorted(groups.items()):
        items.sort(key=lambda e: (e.ts, e.id))
        for name, code, minimum in [("retries", "IDEMPOTENT_RETRY", 1),
                                    ("repeated_refreshes", "HISTORY_REFRESH", 2),
                                    ("abandonment", "ABANDONED", 1)]:
            refs = [e.id for e in items if e.code == code]
            if len(refs) >= minimum:
                result.append({"kind": name, "customer_token": customer, "evidence": refs})
        requests = [e for e in items if e.code == "TRANSFER_REQUESTED"]
        switches = [e.id for e in items if e.code == "CHANNEL_SWITCH"]
        if len({e.channel for e in requests}) > 1:
            switches.extend(e.id for e in requests)
        if switches:
            result.append({"kind": "channel_switching", "customer_token": customer, "evidence": sorted(set(switches))})
    return result
