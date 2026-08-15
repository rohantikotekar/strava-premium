"""Password hashing and policy (AUTH.md §2).

Argon2id with the OWASP baseline parameters. Password policy follows NIST 800-63B:
length over composition rules, breach-corpus checking, no forced rotation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

# OWASP 2025 baseline for Argon2id. Re-tune to the deploy target; the requirement
# is a memory-hard hash, not these exact numbers.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,  # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

#: Verified against on a login for an address that has no account, so that a missing
#: user costs the same wall-clock time as a wrong password. Without this, response
#: timing enumerates registered email addresses.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    ok: bool
    reason: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time verify. A ``None`` hash still burns a real verify."""
    target = password_hash or _DUMMY_HASH
    try:
        _hasher.verify(target, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now require."""
    try:
        return bool(_hasher.check_needs_rehash(password_hash))
    except (InvalidHashError, ValueError):
        return True


def validate_password(password: str, *, email: str | None = None) -> PasswordCheck:
    """Policy check. Length and obvious-content rules only — no composition rules.

    Composition requirements (one upper, one digit, one symbol) push users toward
    predictable patterns without measurably improving strength; NIST 800-63B
    recommends against them.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return PasswordCheck(False, f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        return PasswordCheck(False, f"Keep it under {MAX_PASSWORD_LENGTH} characters.")
    if email and password.strip().lower() == email.strip().lower():
        return PasswordCheck(False, "Your password can't be your email address.")
    if len(set(password)) < 5:
        return PasswordCheck(False, "That's too repetitive to be a good password.")
    return PasswordCheck(True)


async def is_breached(password: str, *, client: httpx.AsyncClient | None = None) -> bool:
    """Check the password against Have I Been Pwned via the k-anonymity range API.

    Only the **first five hex characters** of the SHA-1 hash leave our servers; the
    remainder is matched locally, so HIBP never learns the password.

    Fails **open**: if HIBP is unreachable we allow the signup rather than block a
    real user on a third-party outage. The trade is deliberate.
    """
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=3.0)
    try:
        response = await http.get(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            headers={"Add-Padding": "true"},
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return False
    finally:
        if owns_client:
            await http.aclose()

    for line in response.text.splitlines():
        candidate, _, count = line.partition(":")
        if candidate.strip() == suffix and count.strip() not in ("0", ""):
            return True
    return False
