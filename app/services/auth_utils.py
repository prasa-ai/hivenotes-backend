"""Password hashing and JWT utilities for password-based authentication."""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(
    data: dict,
    secret_key: str,
    algorithm: str,
    expires_minutes: int,
) -> str:
    """Encode *data* as a signed JWT that expires after *expires_minutes* minutes."""
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, secret_key, algorithm=algorithm)
