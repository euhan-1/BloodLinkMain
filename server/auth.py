import hashlib
import math
import os
import secrets
import time
from collections import deque
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


# The 30 weakest passwords from breach-frequency lists (NCSC/HaveIBeenPwned-
# style "most common passwords") that also happen to be >=10 characters or a
# plausible >=10-char variant of one — the ones that would otherwise slip
# past the length + letter-and-digit checks below. Not a general-purpose
# strength estimator (no zxcvbn-style scoring, no dictionary-and-mutation
# search) — proportionate to a facility registration form, not a password
# vault.
_WEAK_PASSWORDS = frozenset({
    "password123", "password1234", "password12345", "12345678910",
    "1234567890", "qwertyuiop12", "qwerty123456", "letmein12345",
    "welcome12345", "iloveyou1234", "admin1234567", "changeme1234",
    "abc123456789", "football1234", "baseball1234", "sunshine1234",
    "princess1234", "dragon123456", "trustno1trustno1", "bloodlink123",
    "bloodlink1234", "hospital12345", "philippines123",
})


def validate_registration_password(password: str) -> Optional[str]:
    """Returns an error message if `password` fails the self-registration
    password policy, or None if it's acceptable.

    Rule: at least 10 characters (longer than the 8-character minimum on
    the forced-reset/self-service-change flows — those accounts either get
    a follow-up forced change or are already logged in proving current
    knowledge; a self-registered account's password is trusted permanently
    from the moment it's set, with must_change_password=false), containing
    at least one letter and one digit (blocks pure-digit or pure-letter
    strings, the cheapest guesses), and not one of a small set of commonly
    breached passwords.
    """
    if len(password) < 10:
        return "password must be at least 10 characters"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "password must contain at least one letter and one digit"
    if password.lower() in _WEAK_PASSWORDS:
        return "this password is too common — please choose a less predictable one"
    return None


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


OTP_CODE_EXPIRY = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5


def generate_otp_code() -> str:
    """A 6-digit code, zero-padded (e.g. "003942") — secrets.randbelow, not
    random, for the same reason as generate_temp_password: this is
    security-sensitive and must not be predictable. Only the code itself; the
    caller (main.py) hashes it before storing and emails the raw value, same
    split as create_password_reset_token/the reset link's raw token."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(code: str) -> str:
    """SHA-256 — fast on purpose. A 6-digit code has only 1e6 possibilities,
    so a slow hash (bcrypt) buys nothing an attempt cap (OTP_MAX_ATTEMPTS)
    doesn't already provide, and this hash runs on every verify request, not
    just at issuance. Same reasoning and same primitive as the reset link's
    token_hash."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class AttemptLimiter:
    """Sliding-window counter: at most `limit` recorded events per `window`
    seconds per key. Callers decide what counts — login records only FAILED
    attempts and clears the key on success, so a legitimate user is never
    slowed down by their own successful sign-ins.

    ponytail: per-process memory — resets on restart and isn't shared across
    instances; move to Redis/DB if Render ever runs more than one."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._events: dict[str, deque] = {}

    def _live(self, key: str) -> Optional[deque]:
        q = self._events.get(key)
        now = self.clock()
        while q and now - q[0] >= self.window:
            q.popleft()
        if q is not None and not q:
            del self._events[key]
            return None
        return q

    def retry_after(self, key: str) -> int:
        """0 = allowed; otherwise whole seconds until the oldest event ages out."""
        q = self._live(key)
        if q is None or len(q) < self.limit:
            return 0
        return max(1, math.ceil(self.window - (self.clock() - q[0])))

    def record(self, key: str) -> None:
        if len(self._events) > 10_000:  # cheap sweep so abandoned keys can't pile up
            for k in list(self._events):
                self._live(k)
        self._events.setdefault(key, deque()).append(self.clock())

    def clear(self, key: str) -> None:
        self._events.pop(key, None)
