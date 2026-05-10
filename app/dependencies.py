from __future__ import annotations

"""
Dependency helpers shared across routers.

require_auth
────────────
Validates the JWT Bearer token on every protected request.
Raises HTTP 401 if the token is missing, malformed, or expired.
Also populates request.state.user_id from the token's ``sub`` claim so
route handlers that read that attribute continue to work.

get_current_user_id
───────────────────
Extracts the authenticated user's ID from request.state (set by require_auth).
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

http_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> dict:
    """Validate JWT Bearer token.  Raises 401 if missing or invalid.

    Populates ``request.state.user_id`` (from the ``sub`` claim) so existing
    route handlers that read that attribute work without changes.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required. Obtain one from POST /api/v1/auth/login.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.jwt_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured.",
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user_id = payload.get("sub", "")
    return payload


async def get_current_user_id(
    payload: dict = Depends(require_auth),
) -> str:
    """Return the authenticated therapist_id from the validated JWT payload."""
    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing the 'sub' claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id
