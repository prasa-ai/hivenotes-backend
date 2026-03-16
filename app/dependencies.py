from __future__ import annotations

"""
Dependency helpers shared across routers.

get_current_user_id
───────────────────
Extracts the authenticated user's ID from the request state. The user_id
is expected to be set by an upstream authentication middleware (e.g. Azure AD
B2C token validation) before the route handler is called.

During development, if no auth middleware is configured, it falls back to the
value of the `X-User-Id` header so the API remains testable without a full
auth stack.

In production, replace the header fallback with a proper JWT/token validation
dependency (e.g. azure-identity, python-jose, etc.).
"""
from fastapi import Depends, HTTPException, Request, status


async def get_current_user_id(request: Request) -> str:
    """
    Return the authenticated user ID.

    Priority:
      1. request.state.user_id  — set by auth middleware after token validation
      2. X-User-Id header       — development/testing fallback only
    """
    # Auth middleware sets this after validating the Bearer token
    user_id: str | None = getattr(request.state, "user_id", None)

    # Dev/test fallback — remove or guard with a settings flag in production
    if not user_id:
        user_id = request.headers.get("X-User-Id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. No user identity could be resolved.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id
