import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = timedelta(hours=24)

# Deliberately much shorter than a normal session — this token can do exactly
# one thing (set a new password via POST /auth/change-password) and nothing
# else, so there's little cost to it expiring quickly if unused.
PASSWORD_RESET_TOKEN_EXPIRY = timedelta(minutes=30)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int, email: str, facility_id: Optional[int], role: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "facility_id": facility_id,
        "role": role,
        "purpose": "access",
        "exp": datetime.now(timezone.utc) + JWT_EXPIRY,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError (or a subclass, e.g. ExpiredSignatureError) on an
    invalid or expired token, and ValueError if the token is structurally
    valid but isn't an access token — in particular, this is what stops a
    password-reset token (see create_password_reset_token) from being usable
    on any endpoint other than POST /auth/change-password: without this
    check it would decode here successfully (same secret, same algorithm)
    and only fail later, unpredictably, wherever the caller happens to reach
    for a claim (like facility_id) the reset token never had."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("purpose") != "access":
        raise ValueError("not an access token")
    return payload


def generate_temp_password() -> str:
    """A random, high-entropy one-time password for admin-onboarded accounts
    (see POST /admin/facilities) — 16 URL-safe characters, ~96 bits of
    entropy, no email/SMS provider to deliver it through yet so it's handed
    back directly in that endpoint's response for the admin to relay."""
    return secrets.token_urlsafe(12)


def create_password_reset_token(user_id: int, email: str) -> str:
    """A narrowly-scoped token that can only be used against
    POST /auth/change-password — see decode_password_reset_token. Issued
    instead of a normal access token whenever must_change_password is true,
    so a forced-reset account has no way to reach any other endpoint until
    the password is actually changed."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": "password_reset",
        "exp": datetime.now(timezone.utc) + PASSWORD_RESET_TOKEN_EXPIRY,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_password_reset_token(token: str) -> dict:
    """Raises jwt.PyJWTError on an invalid/expired token, and ValueError if
    the token is structurally valid but isn't actually a password-reset
    token (e.g. someone passed a normal access token here instead)."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("purpose") != "password_reset":
        raise ValueError("not a password-reset token")
    return payload
