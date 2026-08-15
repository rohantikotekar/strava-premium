-- Runs once, on first boot of the postgres container.
--
-- Creates the NON-SUPERUSER role the application connects as. This matters:
-- PostgreSQL exempts superusers from row-level security unconditionally, so
-- running the app as the `sp` superuser would silently disable every tenant
-- isolation policy in the schema (CLAUDE.md §4.5) — and the app would appear to
-- work perfectly right up until it leaked another user's data.

CREATE ROLE sp_app WITH LOGIN PASSWORD 'sp_dev_password';

GRANT CONNECT ON DATABASE strava_premium TO sp_app;

\connect strava_premium

-- Postgres 15+ revokes CREATE on the public schema from PUBLIC, and Alembic needs
-- it to create tables.
ALTER SCHEMA public OWNER TO sp_app;
GRANT ALL ON SCHEMA public TO sp_app;

-- Extensions require superuser, so they are created here rather than in the
-- migration — the app role deliberately does not have the privilege.
--   pgcrypto: gen_random_uuid() for primary keys
--   citext:   case-insensitive email, so Rider@x.com and rider@x.com are one account
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
