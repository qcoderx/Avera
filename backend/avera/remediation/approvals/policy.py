from __future__ import annotations

from avera.remediation.models import ApprovalKind, ApprovalLevel, Role

APPROVAL_POLICY: dict[ApprovalKind, tuple[ApprovalLevel, Role]] = {
    ApprovalKind.SMS: (ApprovalLevel.CUSTOMER_DISPUTE, Role.CUSTOMER_DISPUTE),
    ApprovalKind.RECONCILIATION: (ApprovalLevel.CUSTOMER_DISPUTE, Role.CUSTOMER_DISPUTE),
    ApprovalKind.CONFIG: (ApprovalLevel.ENGINEER, Role.ENGINEER),
    ApprovalKind.CODE: (ApprovalLevel.ENGINEER, Role.ENGINEER),
}


def policy_for(kind: ApprovalKind) -> tuple[ApprovalLevel, Role]:
    return APPROVAL_POLICY[kind]
