"""JWT authentication for human approval decisions.

Tokens are issued by the bank's identity provider; this API only verifies them.
"""

from __future__ import annotations

import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from avera.remediation.approvals import Approver
from avera.remediation.models import Role

_bearer = HTTPBearer(auto_error=False)


async def current_approver(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Approver:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer JWT required", headers={"WWW-Authenticate": "Bearer"})

    secret = os.getenv("AVERA_APPROVER_JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="JWT verification is not configured")

    issuer = os.getenv("AVERA_APPROVER_JWT_ISSUER", "avera")
    audience = os.getenv("AVERA_APPROVER_JWT_AUDIENCE", "avera-api")
    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            leeway=30,
            options={"require": ["sub", "role", "iss", "aud", "iat", "exp"]},
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 120:
            raise ValueError("invalid subject")
        role = Role(claims.get("role"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired approver JWT",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    return Approver(id=subject, role=role)
