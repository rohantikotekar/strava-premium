"""Redis-backed progressive throttling for auth endpoints (AUTH.md §5).

Never a permanent lockout — that is a denial-of-service vector against any known
email address. Failures buy an increasing delay that decays on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import redis.asyncio as aioredis
from sp_core.config import get_settings

#: Failures before throttling starts, and the ceiling on the backoff.
_BASE_DELAY_S = 30
_MAX_DELAY_S = 15 * 60
_WINDOW_S = 15 * 60

#: Per-scope allowance before throttling begins.
#:
#: Login is deliberately tight — a handful of wrong passwords is already unusual.
#: Signup is looser because it is counted per *IP*, and an office, gym, campus or
#: any CGNAT provider puts hundreds of legitimate people behind one address; five
#: would lock out a whole building after five signups.
_FREE_ATTEMPTS: dict[str, int] = {
    "login_email": 5,
    "login_ip": 10,
    "signup": 30,
}
_DEFAULT_FREE_ATTEMPTS = 5


def _allowance(scope: str) -> int:
    return _FREE_ATTEMPTS.get(scope, _DEFAULT_FREE_ATTEMPTS)


@lru_cache
def redis_client() -> aioredis.Redis:
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


@dataclass(frozen=True, slots=True)
class ThrottleState:
    allowed: bool
    retry_after_s: int = 0


def _key(scope: str, identifier: str) -> str:
    return f"throttle:{scope}:{identifier.lower()}"


async def check(scope: str, identifier: str | None) -> ThrottleState:
    """Is this identifier allowed to attempt right now?"""
    if not identifier:
        return ThrottleState(True)
    try:
        client = redis_client()
        failures = int(await client.get(_key(scope, identifier)) or 0)
    except Exception:
        return ThrottleState(True)

    allowance = _allowance(scope)
    if failures < allowance:
        return ThrottleState(True)

    delay = min(_BASE_DELAY_S * 2 ** (failures - allowance), _MAX_DELAY_S)
    try:
        ttl = int(await redis_client().ttl(_key(scope, identifier)) or 0)
    except Exception:
        return ThrottleState(True)

    # The counter's remaining TTL doubles as "how long until this clears".
    return ThrottleState(False, retry_after_s=max(min(delay, ttl), 1))


async def record_failure(scope: str, identifier: str | None) -> None:
    if not identifier:
        return
    try:
        client = redis_client()
        key = _key(scope, identifier)
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, _WINDOW_S)
    except Exception:
        return


async def clear(scope: str, identifier: str | None) -> None:
    if not identifier:
        return
    try:
        await redis_client().delete(_key(scope, identifier))
    except Exception:
        return
