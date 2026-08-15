"""Row-level security: the test that must never be allowed to fail.

Tenant isolation is enforced in the database, not in application code
(CLAUDE.md §4.5). This asserts that a query with a *deliberately forgotten*
``WHERE user_id = ...`` still cannot read another tenant's rows.

Requires the local stack: ``docker compose up -d`` then ``make migrate``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sp_db.models import Activity, User
from sp_db.session import sync_session_factory, sync_session_scope
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


def _make_user(session, email: str) -> uuid.UUID:
    user = User(email=email, password_hash="x")
    session.add(user)
    session.flush()
    return user.id


def _make_activity(session, user_id: uuid.UUID, name: str) -> None:
    session.add(
        Activity(
            user_id=user_id,
            source="bulk_csv",
            start_time_utc=datetime.now(UTC),
            start_time_local=datetime.now(UTC).replace(tzinfo=None),
            elapsed_time_s=3600,
            sport_type="Run",
            sport_group="run",
            name=name,
            distance_m=10000.0,
        )
    )


@pytest.fixture
def two_tenants():
    """Create two users with one activity each, then clean up."""
    suffix = uuid.uuid4().hex[:8]
    with sync_session_factory()() as session:
        # Users table has no RLS policy (auth lookups happen before a tenant is
        # known), so it is written without a tenant set.
        alice = _make_user(session, f"alice-{suffix}@example.test")
        bob = _make_user(session, f"bob-{suffix}@example.test")
        session.commit()

    with sync_session_scope(alice) as session:
        _make_activity(session, alice, "Alice morning run")
    with sync_session_scope(bob) as session:
        _make_activity(session, bob, "Bob evening run")

    yield alice, bob

    with sync_session_factory()() as session:
        session.execute(text("SELECT set_config('app.user_id', '', true)"))
        for user_id in (alice, bob):
            session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
        session.commit()


class TestRowLevelSecurity:
    def test_unqualified_select_returns_only_the_current_tenant(self, two_tenants):
        """The whole point: no WHERE clause, and still no leak."""
        alice, bob = two_tenants

        with sync_session_scope(alice) as session:
            rows = session.execute(select(Activity)).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "Alice morning run"
            assert rows[0].user_id == alice

        with sync_session_scope(bob) as session:
            rows = session.execute(select(Activity)).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "Bob evening run"

    def test_cannot_read_another_tenant_even_by_explicit_id(self, two_tenants):
        """Naming the other user's id explicitly must return nothing, not their row."""
        alice, bob = two_tenants

        with sync_session_scope(alice) as session:
            rows = session.execute(select(Activity).where(Activity.user_id == bob)).scalars().all()
            assert rows == []

    def test_no_tenant_set_sees_nothing(self, two_tenants):
        """Fail closed: an unset app.user_id must not mean 'see everything'."""
        with sync_session_scope(None) as session:
            rows = session.execute(select(Activity)).scalars().all()
            assert rows == []

    def test_cannot_write_a_row_belonging_to_another_tenant(self, two_tenants):
        """The policy's WITH CHECK clause blocks cross-tenant inserts."""
        alice, bob = two_tenants

        with pytest.raises(Exception) as exc_info, sync_session_scope(alice) as session:
            _make_activity(session, bob, "Forged row")
        assert "policy" in str(exc_info.value).lower() or "violates" in str(exc_info.value).lower()

    def test_app_role_is_not_a_superuser(self):
        """A superuser bypasses RLS unconditionally, which would silently disable
        every policy above. This asserts the deployment got that right."""
        with sync_session_factory()() as session:
            is_super = session.execute(
                text("SELECT usesuper FROM pg_user WHERE usename = current_user")
            ).scalar_one()
            assert is_super is False, (
                "The app is connected as a superuser — RLS is being bypassed. "
                "Connect as sp_app (see infra/postgres-init.sql)."
            )
