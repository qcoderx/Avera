"""Wires the remediation domain together. Koded's orchestrator calls `on_validated` after the 85% handoff."""

from __future__ import annotations

from dataclasses import dataclass

from avera.remediation.approvals import ApprovalService, InMemoryApprovalRepository
from avera.remediation.audit import AuditLog, InMemoryAuditLog
from avera.remediation.blast_radius import BlastRadiusAgent, BlastRadiusConfig
from avera.remediation.models import ValidatedHandoff
from avera.remediation.ports import Ports
from avera.remediation.resolution import ResolutionAgent
from avera.remediation.store import RemediationCase, RemediationStore


@dataclass
class Remediation:
    ports: Ports
    audit: AuditLog
    approvals: ApprovalService
    store: RemediationStore
    blast_radius: BlastRadiusAgent
    resolution: ResolutionAgent

    def on_validated(self, handoff: ValidatedHandoff) -> RemediationCase:
        case = self.blast_radius.handle(handoff)
        self.resolution.start(case)
        return case


def build_remediation(ports: Ports, config: BlastRadiusConfig = BlastRadiusConfig()) -> Remediation:
    audit = InMemoryAuditLog()
    approvals = ApprovalService(InMemoryApprovalRepository(), audit, ports.events, ports.clock)
    store = RemediationStore()
    return Remediation(
        ports=ports,
        audit=audit,
        approvals=approvals,
        store=store,
        blast_radius=BlastRadiusAgent(ports, approvals, audit, store, config),
        resolution=ResolutionAgent(ports, approvals, audit, store),
    )
