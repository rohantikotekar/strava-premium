# CLAUDE.md

Engineering guide for this repository. Read this before writing code. It is
binding: if a change conflicts with a rule here, either follow the rule or change
this file in the same PR with a reason.

---

## 1. What this project is

**Strava Premium (working name)** — a self-hosted analytics layer over a user's own
Strava data.

- The user uploads their **Strava bulk export `.zip`** (up to 10+ years of
  `.csv`, `.fit`, `.gpx`, `.tcx` files). We parse it into a canonical schema.
- After OAuth, we keep the account **live via the Strava Webhook Events API** —
  new activities arrive by push, not polling.
- We compute and render the analytics Strava puts behind its paid tier
  (fitness/freshness, power curve, best-effort progression, HR-zone trends, gear
  mileage, route heatmaps) plus things it doesn't offer at all.

**The core product constraint:** every user's data is different. A user with a
chest strap and a power meter gets different charts than someone with a phone-only
GPS. The dashboard is **capability-driven** — see §5.

Full docs live in [docs/](docs/):

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, tech stack + justification, scaling path |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | Postgres schema, object-store layout, canonical models |
| [INGESTION.md](docs/INGESTION.md) | Bulk-export pipeline and the fast-load strategy |
| [AUTH.md](docs/AUTH.md) | Account creation, email/password + Google login, sessions |
| [STRAVA_API.md](docs/STRAVA_API.md) | Connecting Strava (data, not login), webhooks, rate limits |
| [FEATURES.md](docs/FEATURES.md) | Feature → metric → chart mapping, v1/v2/v3 |
| [FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md) | UI/UX plan, chart specs, design tokens |
| [EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) | Milestones, cut lines, definition of done |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Hosting plan, launch checklist, scaling path |

---

## 2. Repository layout

```
strava-premium/
├── apps/
│   ├── api/                    # FastAPI service (HTTP only, no heavy work)
│   │   └── src/sp_api/
│   │       ├── main.py
│   │       ├── routers/        # one module per resource
│   │       ├── schemas/        # Pydantic request/response models
│   │       └── deps.py         # DI: db session, current_user, settings
│   ├── worker/                 # Celery workers (all heavy work)
│   │   └── src/sp_worker/
│   │       ├── tasks/
│   │       └── celery_app.py
│   └── web/                    # React + Vite frontend
│       └── src/
│           ├── routes/
│           ├── features/       # feature-sliced: charts, upload, activity, ...
│           ├── components/ui/  # shadcn primitives
│           └── lib/api/        # generated OpenAPI client — do not hand-edit
├── packages/
│   ├── core/                   # sp_core: domain models, canonical schema, metrics
│   │   └── src/sp_core/
│   │       ├── canonical/      # CanonicalActivity, StreamSet — the contract
│   │       ├── parsers/        # fit.py, gpx.py, tcx.py, csv_index.py, api_json.py
│   │       ├── metrics/        # pure functions: TSS, CTL/ATL, power curve, zones
│   │       └── storage/        # object store + Parquet read/write
│   └── db/                     # sp_db: SQLAlchemy models + Alembic migrations
├── docs/
├── infra/                      # docker-compose, Dockerfiles, deploy config
└── tests/
```

**Dependency direction is one-way and enforced:**

```
web  →  api  →  core  →  db
        worker ↗
```

`core` must never import from `api` or `worker`. `api` must never import from
`worker` (it enqueues by task *name* through a thin `enqueue()` helper). This is
what lets us split the worker onto its own machines without a refactor.

---

## 3. Language, tooling, style

### Python (3.12)

- **uv** for dependency management and virtualenvs. One `uv.lock` at the root; the
  repo is a uv workspace. Never `pip install` into the project env.
- **Ruff** for lint + format (replaces black, isort, flake8). Line length 100.
- **mypy** in strict mode on `packages/core` and `packages/db`; non-strict on apps.
- **Pydantic v2** for all boundaries (HTTP, task payloads, parser output).
  Dataclasses only for internal hot-path structs where validation costs too much.
- **SQLAlchemy 2.0** typed ORM (`Mapped[...]` / `mapped_column`). No legacy Query API.
- **psycopg 3** driver. Async in the API, sync in workers.

Rules:

- Type-annotate everything public. `Any` needs a comment explaining why.
- No bare `except:`. Catch the narrowest exception; re-raise or log with context.
- Use `structlog` — **structured logs only**, never f-string log messages:
  `log.info("parse.activity.done", activity_id=..., ms=...)`, not
  `log.info(f"parsed {id}")`.
- Every parser and metric function in `core` is **pure**: input bytes/frames →
  output models. No DB, no network, no filesystem, no clock reads (pass `now` in).
  This is why they are testable against real files.
- Money/distance/time units: store SI in the DB (metres, seconds, watts, joules).
  Convert to user units **only in the frontend**. No exceptions — unit confusion is
  the #1 bug source in fitness apps.
- Timestamps: store `TIMESTAMPTZ` in UTC, always. Also store the activity's
  `local_start_time` (naive) and `utc_offset` separately, because "did I run in the
  morning?" needs local wall-clock and a UTC timestamp cannot answer it.

### TypeScript / React (Node 22+)

- **pnpm**, not npm or yarn.
- **TypeScript strict**. `any` is a lint error; use `unknown` + narrowing.
- **Biome** for lint + format (fast; replaces ESLint + Prettier).
- **Never hand-write API types.** `pnpm gen:api` regenerates
  `src/lib/api/schema.d.ts` from the FastAPI OpenAPI doc via `openapi-typescript`,
  consumed through `openapi-fetch`. If the frontend needs a field, add it to the
  Pydantic response model first.
- **TanStack Query owns all server state.** No server data in `useState`,
  Zustand, or Context. Client-only state (open dialogs, selected tab) may use
  `useState`/`nuqs` (URL state preferred so views are shareable).
- Components: function components + hooks only. No class components, no `forwardRef`
  unless a UI primitive genuinely needs it (React 19 passes `ref` as a prop).
- File naming: `PascalCase.tsx` for components, `camelCase.ts` for everything else.

---

## 4. Architectural rules

1. **The API layer does no heavy work.** Any operation that can exceed ~200ms
   (parsing, metric recomputation, Strava backfill) is a Celery task. HTTP handlers
   validate, enqueue, and return a job id.
2. **All writes are idempotent.** Every ingest path keys on a natural id
   (`strava_activity_id`, or `sha256(file bytes)` for orphan files). Re-uploading
   the same zip must be a no-op, not a duplicate. Tasks must be safe to retry —
   Celery *will* retry them.
3. **Raw data is immutable and kept.** The uploaded zip and every extracted stream
   Parquet stay in object storage. Derived tables are always rebuildable by
   replaying from raw. When a metric formula changes, we recompute, we don't
   migrate numbers.
4. **Time-series does not go in Postgres.** Per-point streams (lat/lng, HR, power,
   cadence, altitude) live as Parquet in object storage, one file per activity.
   Postgres holds summaries and precomputed aggregates only. Rationale and numbers
   in [ARCHITECTURE.md](docs/ARCHITECTURE.md#why-not-time-series-rows-in-postgres).
5. **Tenant isolation is enforced in the database, not in application code.** Every
   user-scoped table carries `user_id` and has a Postgres RLS policy. The request
   scope sets `SET LOCAL app.user_id`. A forgotten `WHERE user_id = ...` must not be
   able to leak data.
6. **Never fail the whole import for one bad file.** A corrupt `.fit` among 3,000
   files gets recorded in `ingest_items` with status `failed` + the error, and the
   import continues. Partial success is the normal outcome and the UI says so.
7. **Unknown fields are preserved, never dropped.** Anything a parser doesn't map to
   a canonical column goes into a JSONB `extra` field. Today's unknown field is next
   quarter's feature.

---

## 5. The capability model (read this twice)

Different users have wildly different data. Hardcoding a "power curve" chart into
the dashboard breaks for the 90% of users who have never owned a power meter.

So: during ingest we populate **`user_capabilities`** — which streams, sports, and
fields this user actually has, with coverage counts and date ranges. Then:

- `GET /me/capabilities` returns that set.
- The frontend **renders the chart registry filtered by capabilities.** Each chart
  declares `requires: ["stream.heartrate"]` and a `minCoverage`.
- A chart with no data is **not rendered as an empty chart.** It either disappears
  or appears in a "Unlock more" section explaining what device/data would enable it.

When you add a feature, you add its capability requirement. No exceptions — a chart
that assumes data exists is a bug report waiting to happen.

---

## 6. Testing

- **pytest**, with `pytest-asyncio` and `testcontainers` for a real Postgres. No
  SQLite-as-a-stand-in; we use Postgres-specific features (JSONB, RLS, `COPY`).
- **Golden-file parser tests are mandatory.** `tests/fixtures/` holds real (scrubbed)
  `.fit` / `.gpx` / `.tcx` / `activities.csv` samples covering: run, ride, swim,
  indoor trainer, no-GPS, no-HR, multisport, paused activity, corrupt/truncated file,
  a 6-hour ultra, an activity with 0 distance. A parser PR without a new fixture for
  the case it fixes will be rejected.
- **Metric tests use hand-computed expected values** in the test file with the
  formula cited. Never assert against whatever the code currently outputs.
- Frontend: **Vitest** for logic, **Playwright** for the two flows that must never
  break — (a) OAuth → dashboard, (b) upload zip → progress → charts render.
- Target: ≥85% coverage on `packages/core`. Coverage elsewhere is not a gate.

---

## 7. Git & GitHub

- **Trunk-based.** `main` is always deployable. Branch names:
  `feat/…`, `fix/…`, `chore/…`, `docs/…`, `refactor/…`.
- **Conventional Commits** — `feat(ingest): stream zip members without extraction`.
  The scope should be a package or app name.
- **PRs are small and single-purpose.** If a PR touches more than ~400 changed lines
  of non-generated code, it should probably be two PRs.
- Every PR body states: what changed, why, how it was verified, and any migration or
  backfill needed.
- **Squash merge only.** The squashed subject line becomes the changelog entry.
- CI must pass before merge: `ruff check` + `ruff format --check` + `mypy` +
  `pytest` + `biome ci` + `tsc --noEmit` + `vitest`. CI is not advisory.
- **Migrations:** one Alembic revision per PR, max. Must be reversible or explicitly
  marked irreversible with a comment. Never edit a migration that has run in any
  deployed environment.
- Never commit: `.env`, tokens, real user exports, `.fit` files containing real GPS
  from a home address. Fixtures must be scrubbed (offset coordinates, fake dates).

---

## 8. Security & privacy

This app holds **precise, decade-long location history**. Treat it accordingly.

- Identity is email+password or Google OAuth; Strava is a separate data connection,
  not a login method. Full design, threat model, and abuse controls in
  [AUTH.md](docs/AUTH.md) — read it before touching anything under `/auth/*`.
- Passwords are hashed with **Argon2id** (`argon2-cffi`), never anything reversible,
  never logged, never included in any response model. Verified against the
  HaveIBeenPwned range API at signup/reset (k-anonymity — only a 5-char hash prefix
  leaves our servers).
- Sessions are opaque server-side tokens (not JWTs), httpOnly/Secure/SameSite=Lax
  cookies, revocable individually or all-at-once. A password reset revokes every
  existing session for that user.
- Strava access/refresh tokens are encrypted at rest (`pgcrypto` or app-level
  Fernet with a KMS-held key). Never logged, never returned by any endpoint.
- Uploads go **browser → object store via presigned URL**, never through the API.
- All object-store reads are served through short-lived presigned GETs scoped to the
  owning user. Buckets are private.
- Log redaction: coordinates, tokens, and email are on the `structlog` redaction list.
- **Deauthorization deletes everything.** Strava's API terms require it and the
  webhook tells us. Implement it as a real cascade, not a soft-delete flag.
- Never send user activity data to a third party — including LLM APIs — without an
  explicit, separately-granted user opt-in. Strava's API agreement forbids using
  their data to train models. See [STRAVA_API.md](docs/STRAVA_API.md#compliance).

---

## 9. Working conventions for Claude

- **Read the relevant doc in `docs/` before implementing.** They contain decisions
  already made; re-deriving them wastes time and produces drift.
- Prefer editing existing modules over adding new ones. Don't create a parallel
  utility when one exists.
- Don't add a dependency without saying why in the PR. Especially: no new HTTP
  client (use `httpx`), no new date library (use `whenever` / stdlib), no new chart
  library beyond the three in [FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md).
- **Do not write throwaway migration or backfill scripts into the repo root.** They
  belong in `apps/worker/src/sp_worker/tasks/backfills/` with a dated name.
- When something is genuinely ambiguous, implement the simplest version that
  satisfies the doc and note the assumption in the PR — don't stall.
- Don't add comments that restate the code. Comment the *why*: a magic constant, a
  Strava quirk, a workaround for a device's malformed FIT output. Those are gold.
- All new charts must follow the chart rules in
  [FRONTEND_DESIGN.md §Chart system](docs/FRONTEND_DESIGN.md#chart-system). Two
  hard ones: **never a dual y-axis**, and **categorical colors are assigned by
  entity in fixed slot order, never by rank or index in a filtered list.**
</content>
</invoke>
