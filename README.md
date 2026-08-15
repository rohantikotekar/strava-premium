# Strava Premium

Self-hosted analytics over your own Strava data. Upload the bulk export Strava gives
you (10+ years of `.csv`, `.fit`, `.gpx`, `.tcx`), connect your account, and get the
analysis Strava puts behind its paid tier — plus a few things it doesn't offer at all.

> **Status: planning.** This repo currently contains the design documents. Code starts
> at M0 in the [execution plan](docs/EXECUTION_PLAN.md).

---

## How it works

```
Bulk export .zip  ──▶  two-phase parser  ──▶  Postgres (summaries + aggregates)
(10 years of history)                          Parquet in object storage (streams)
                                                        │
Strava OAuth + webhooks ──▶ live sync ──────────────────┤
(everything from now on)                                ▼
                                              React dashboard, charts
                                              rendered per your data
```

Two ideas carry the design:

**1. The export is for catching up; the API is for keeping up.** Strava's API rate
limit is per-*application* and shared across every user, so backfilling a decade
through it doesn't scale. The user's own export does — and it also means the product
survives an API terms change.

**2. Two-phase ingest.** `activities.csv` inside the export holds ~90% of what the
dashboard needs and parses in seconds. It loads first, the dashboard goes live, and
the thousands of `.fit` files are decoded in parallel behind it, upgrading charts in
place. Time-to-first-chart: ~20 seconds, not 15 minutes.

---

## Documentation

| Doc | Read it for |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Engineering rules, repo layout, conventions — **read first** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, tech choices and their justification, scaling path |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Canonical schema, Postgres tables, object-store layout |
| [docs/AUTH.md](docs/AUTH.md) | Account creation, email/password + Google login, sessions, abuse controls |
| [docs/INGESTION.md](docs/INGESTION.md) | The bulk-export pipeline and how it's made fast |
| [docs/STRAVA_API.md](docs/STRAVA_API.md) | Connecting Strava (data, not login), webhooks, rate limits |
| [docs/FEATURES.md](docs/FEATURES.md) | Every feature → the metric → the chart |
| [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md) | UI plan, chart system, edge cases, copy |
| [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) | Milestones, cut lines, what to do today |

---

## Stack

| Layer | Choice |
|---|---|
| Frontend | React 19, TypeScript, Vite, TanStack Query + Router, Tailwind v4, shadcn/ui |
| Charts | Recharts (dashboard) · uPlot (dense streams) · MapLibre + deck.gl (maps) |
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, psycopg 3 |
| Auth | Argon2id (`argon2-cffi`) password hashing, Google OIDC (`google-auth`), opaque server-side sessions — Strava is a separate data connection, not identity |
| Workers | Celery 5 + Redis, split into CPU (ingest) and I/O (Strava sync) pools |
| Database | PostgreSQL 17 — summaries, aggregates, rollups, RLS tenant isolation |
| Streams | Parquet + zstd in S3-compatible storage (MinIO dev, Cloudflare R2 prod) |
| Parsing | fitdecode, gpxpy, python-tcxreader, pandas, pyarrow |
| Tooling | uv, ruff, mypy, pytest + testcontainers · pnpm, Biome, Vitest, Playwright |

Rationale for each — including why *not* Django, Next.js, TimescaleDB, or
per-point rows in Postgres — is in
[ARCHITECTURE.md §3](docs/ARCHITECTURE.md#3-tech-stack-and-why).

---

## Running it locally

Prerequisites: **Docker**, **Python 3.12+**, **Node 22+**,
[`uv`](https://docs.astral.sh/uv/) and `pnpm` (`npm i -g pnpm`).

```bash
git clone <this repo> && cd strava-premium
cp .env.example .env          # defaults match docker compose; nothing to edit to start

uv sync                       # Python deps into .venv
cd apps/web && pnpm install && cd ../..

docker compose up -d          # postgres + redis + minio (~10s)
cd packages/db && uv run alembic upgrade head && cd ../..
```

Then three terminals:

| Terminal | Command | What it is |
|---|---|---|
| 1 | `uv run python -m sp_api` | API on :8000 |
| 2 | `uv run celery -A sp_worker.celery_app worker --pool=solo -Q ingest` | Ingest worker |
| 3 | `cd apps/web && pnpm dev` | UI on :5173 |

Open **http://localhost:5173**, create an account, and go to **Import**.

> `make up` / `make api` / `make worker` / `make web` wrap the same commands if you
> have `make` (Git Bash on Windows).

**Don't have a Strava export yet?** Generate a realistic synthetic one — a mix of
runs with HR, rides with power, indoor sessions, a strength session with no
distance, and a deliberately corrupt file:

```bash
uv run python -c "from tests.fixtures.export_builder import build_export_zip; \
open('sample-export.zip','wb').write(build_export_zip(n_activities=40))"
```

Upload `sample-export.zip` through the Import page.

### Testing

```bash
uv run pytest -m "not integration"   # 150+ unit tests, no services needed
uv run pytest -m integration         # needs the stack + API + worker running
cd apps/web && pnpm test             # frontend unit tests
```

### Notes for Windows

Two things that will bite you, both already handled in the code:

- Run the API via **`python -m sp_api`**, not `uvicorn sp_api.main:app`. psycopg's
  async mode cannot run on Windows' default `ProactorEventLoop`, and the loop is
  created before the app module is imported — [`__main__.py`](apps/api/src/sp_api/__main__.py)
  builds a compatible loop first.
- Use `--pool=solo` for Celery. The default prefork pool doesn't work on Windows.

### Connecting Strava (optional)

The app is fully usable from a bulk export alone. To also sync new activities, add
`STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` to `.env`. Webhooks need a public HTTPS
callback, so local development uses a tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

---

## Status

**v1 is implemented and runs end to end**: account creation (email+password and
Google), presigned direct upload, the two-phase ingest pipeline, the metric suite,
capability-gated charts, and the React dashboard.

Verified on a synthetic 37-activity export: **dashboard usable 1.2 s after upload,
full deep parse complete in 3.2 s**, with one deliberately-corrupt file recorded as
unreadable and the import completing anyway.

| Area | State |
|---|---|
| Auth, sessions, RLS tenant isolation | Done, tested |
| Bulk export ingest (CSV fast path + FIT/GPX/TCX) | Done, tested |
| Metrics (TSS, TRIMP, CTL/ATL/TSB, curves, zones, PRs) | Done, hand-computed tests |
| Charts + capability gating | Done, tested |
| Strava OAuth connect, webhooks, live sync | **Schema and docs only** — see [STRAVA_API.md](docs/STRAVA_API.md) |
| Route maps / heatmap (MapLibre, deck.gl) | Not yet — polylines are stored, nothing renders them |
| Google sign-in | Implemented; needs real credentials to exercise |
| Email delivery | Dev-only: links are logged, not sent |

Remaining v1 work is tracked in [EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md).

---

## Getting your Strava export

Strava → **Settings → My Account → Download or Delete Your Account → Request your
archive.** Strava emails a download link, usually within a few hours. Request it
early — nothing in this project is testable without a real one.

---

## Privacy

This app holds precise, decade-long location history. It reads your Strava data and
never writes to it (no `write` scope is requested). Your login (email/password or
Google) is separate from your Strava connection, so disconnecting Strava removes
your Strava-sourced data without touching your account — deleting the account
itself is a separate, explicit action. Passwords are Argon2id-hashed, uploaded
archives are private, tokens are encrypted at rest, and all data is row-level-
isolated per user. See [AUTH.md](docs/AUTH.md),
[STRAVA_API.md §6](docs/STRAVA_API.md#compliance), and
[CLAUDE.md §8](CLAUDE.md#8-security--privacy).

Not affiliated with Strava. Powered by Strava.
</content>
</invoke>
