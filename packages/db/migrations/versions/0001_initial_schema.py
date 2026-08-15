"""Initial schema: identity, ingest, activities, aggregates, RLS.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from sp_db.models import RLS_TABLES, Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # NOTE: pgcrypto and citext are created by infra/postgres-init.sql as superuser.
    # The app role intentionally lacks CREATE EXTENSION privilege, so attempting it
    # here would fail — and granting it would weaken the role that RLS depends on.

    # Baseline revision: the models are the single source of truth for the initial
    # shape. Every subsequent revision uses explicit op.* calls so the diff is
    # reviewable.
    Base.metadata.create_all(bind=bind)

    # Case-insensitive email. Done after create_all because the ORM type is Text —
    # CITEXT behaves identically to Text for SQLAlchemy but gives us the uniqueness
    # semantics we actually want ("Rohan@x.com" and "rohan@x.com" are one account).
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE CITEXT")
    op.execute("ALTER TABLE auth_identities ALTER COLUMN email_at_link TYPE CITEXT")
    op.execute("ALTER TABLE auth_events ALTER COLUMN email_tried TYPE CITEXT")

    # Tenant isolation enforced by the database, not by application code
    # (CLAUDE.md §4.5). FORCE is required because the app role owns these tables and
    # a table owner is otherwise exempt from its own policies.
    #
    # NOTE: a superuser bypasses RLS unconditionally. The app must connect as a
    # non-superuser role — see infra/postgres-init.sql, which creates `sp_app`.
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (user_id::text = current_setting('app.user_id', true))
                WITH CHECK (user_id::text = current_setting('app.user_id', true))
            """
        )

    # Partial index supporting the webhook/undone-work scan.
    op.execute(
        "CREATE INDEX ingest_items_pending ON ingest_items (upload_id) "
        "WHERE status = 'pending'"
    )
    # The dashboard's hottest query: this user's activities, newest first.
    op.execute(
        "CREATE INDEX activities_extra_gin ON activities USING GIN (extra jsonb_path_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    Base.metadata.drop_all(bind=bind)
