"""Who has been affected, who complained, who stayed silent, and who is likely to be hit next.

Definitions (frozen with the team):
- Affected: a customer with at least one transaction matching the incident fingerprint inside the incident window.
- Complained: an affected customer who explicitly contacted the bank, on any channel, with a complaint the parser
  matched to one of *their* affected transactions.
- Silent: affected but not complained. Behaviour signals (retries, refreshes, channel switching) never move a
  customer out of silent; they are counted separately as silent_with_signals.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from avera.contracts import Complaint
from avera.remediation.models import AffectedCustomer, BehaviourSignal, BlastRadius, TransactionRecord

PROJECTION_LOOKBACK = timedelta(minutes=5)


def matches(traits: dict[str, str], txn: TransactionRecord, start: datetime, end: datetime) -> bool:
    return start <= txn.ts <= end and all(txn.trait(name) == value for name, value in traits.items())


def compute_blast_radius(
    *,
    blast_id: str,
    incident_id: str,
    traits: dict[str, str],
    window_start: datetime,
    transactions: list[TransactionRecord],
    complaints: list[Complaint],
    signals: list[BehaviourSignal],
    as_of: datetime,
    projection_horizon_minutes: int,
) -> BlastRadius:
    matched = sorted(
        {t.txn_id: t for t in transactions if matches(traits, t, window_start, as_of)}.values(),
        key=lambda t: (t.ts, t.txn_id),
    )
    by_customer: dict[str, list[TransactionRecord]] = defaultdict(list)
    for txn in matched:
        by_customer[txn.customer_token].append(txn)

    owner_of = {t.txn_id: t.customer_token for t in matched}
    complained = {
        c.customer_token
        for c in complaints
        if c.customer_token and c.parsed and c.parsed.matched_txn
        and owner_of.get(c.parsed.matched_txn) == c.customer_token
    }

    signals_by_customer: dict[str, set[str]] = defaultdict(set)
    for s in signals:
        if s.customer_token in by_customer and window_start <= s.ts <= as_of:
            signals_by_customer[s.customer_token].add(s.kind)

    customers = [
        AffectedCustomer(
            customer_token=token,
            txn_ids=[t.txn_id for t in txns],
            exposure_kobo=sum(t.amount_kobo for t in txns),
            first_affected_at=txns[0].ts,
            complained=token in complained,
            behaviour_signals=sorted(signals_by_customer.get(token, ())),
        )
        for token, txns in sorted(by_customer.items())
    ]
    silent = [c for c in customers if not c.complained]
    projected, basis = _project_new_customers(customers, as_of, projection_horizon_minutes)

    return BlastRadius(
        id=blast_id,
        incident_id=incident_id,
        matched_on=traits,
        window_start=window_start,
        window_end=as_of,
        txn_count=len(matched),
        customer_count=len(customers),
        complained_count=len(customers) - len(silent),
        silent_count=len(silent),
        silent_with_signals_count=sum(1 for c in silent if c.behaviour_signals),
        projected_count=projected,
        projection_basis=basis,
        exposure_kobo=sum(c.exposure_kobo for c in customers),
        customers=customers,
        computed_at=as_of,
    )


def _project_new_customers(
    customers: list[AffectedCustomer], as_of: datetime, horizon_minutes: int
) -> tuple[int, str]:
    recent = sum(1 for c in customers if c.first_affected_at >= as_of - PROJECTION_LOOKBACK)
    lookback_minutes = PROJECTION_LOOKBACK.total_seconds() / 60
    rate = recent / lookback_minutes
    basis = (
        f"{recent} newly affected customers in the last {lookback_minutes:.0f} min "
        f"({rate:.1f}/min) x {horizon_minutes} min until containment"
    )
    return math.ceil(rate * horizon_minutes), basis
