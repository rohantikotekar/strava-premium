"""Engines, sessions, and tenant scoping.

Async in the API, sync in workers (CLAUDE.md §3).

**Tenant isolation is enforced in the database, not in application code**
(CLAUDE.md §4.5). Every request/task opens a transaction and sets
``app.user_id``; the RLS policies do the rest, so a forgotten
``WHERE user_id = ...`` cannot leak another user's decade of GPS traces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from uuid import UUID

from sp_core.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker


@lru_cache
def async_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.async_database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache
def sync_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.sync_database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache
def async_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine(), expire_on_commit=False, class_=AsyncSession)


@lru_cache
def sync_session_factory() -> sessionmaker[Session]:
    return sessionmaker(sync_engine(), expire_on_commit=False)


async def set_tenant(session: AsyncSession, user_id: UUID | None) -> None:
    """Scope the current transaction to one user.

    ``SET LOCAL`` is transaction-bound, so it cannot leak into the next request
    that borrows this pooled connection.
    """
    if user_id is None:
        await session.execute(text("SELECT set_config('app.user_id', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
        )


def set_tenant_sync(session: Session, user_id: UUID | None) -> None:
    if user_id is None:
        session.execute(text("SELECT set_config('app.user_id', '', true)"))
    else:
        session.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})


@asynccontextmanager
async def session_scope(user_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Async session bound to one tenant, committed on clean exit."""
    async with async_session_factory()() as session:
        await set_tenant(session, user_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def sync_session_scope(user_id: UUID | None = None) -> Iterator[Session]:
    """Sync equivalent for Celery tasks."""
    with sync_session_factory()() as session:
        set_tenant_sync(session, user_id)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
