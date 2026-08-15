"""Dependency injection: DB session, current user, tenant scoping."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sp_core.config import Settings, get_settings
from sp_core.security.tokens import hash_token
from sp_db.models import Session as SessionRow
from sp_db.models import User
from sp_db.session import async_session_factory, set_tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SESSION_COOKIE = "sp_session"


async def db_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Commits on clean exit, rolls back on error."""
    async with async_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def current_user_optional(
    session: DbSession,
    sp_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User | None:
    """Resolve the session cookie to a user, and scope the transaction to them.

    Setting ``app.user_id`` here is what activates every RLS policy for the rest of
    the request — a handler that forgets a ``WHERE user_id`` still cannot read
    another tenant's rows (CLAUDE.md §4.5).
    """
    if not sp_session:
        return None

    row = (
        await session.execute(
            select(SessionRow).where(SessionRow.token_hash == hash_token(sp_session))
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        return None

    # Sliding expiry, written at most once a minute to avoid a write per request.
    if (now - row.last_seen_at.replace(tzinfo=UTC)).total_seconds() > 60:
        row.last_seen_at = now
        user.last_seen_at = now

    await set_tenant(session, user.id)
    return user


async def current_user(
    user: Annotated[User | None, Depends(current_user_optional)],
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


async def verified_user(user: Annotated[User, Depends(current_user)]) -> User:
    """For actions gated behind email verification (AUTH.md §2)."""
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email address first. Check your inbox for the link.",
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(current_user_optional)]
VerifiedUser = Annotated[User, Depends(verified_user)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
