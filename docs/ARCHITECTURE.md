# Architecture

## 1. The shape of the problem

Three workloads with completely different profiles are hiding in this product:

| Workload | Profile | Implication |
|---|---|---|
| **Bulk import** | One-shot, 1–10 GB zip, 500–5,000 files, CPU-bound binary decoding, minutes to hours | Must be async, parallel, resumable, and must not touch the web tier |
| **Live sync** | Trickle, 1–5 activities/day/user, network-bound, hard third-party rate limit **shared across all users** | Must be a global token-bucket queue, not per-user polling |
| **Dashboard reads** | Interactive, sub-300ms, many small aggregate queries | Must read precomputed rollups, never scan raw points |

Almost every bad design here comes from letting one of these bleed into another —
most commonly, parsing FIT files inside an HTTP request, or charting off raw
per-second rows. The architecture below keeps them separate.

---

## 2. System diagram

```
┌──────────────┐        presigned PUT (direct)        ┌──────────────────┐
│   Browser    │─────────────────────────────────────▶│  Object Storage  │
│  React SPA   │                                       │  S3 / R2 / MinIO │
└──────┬───────┘                                       └────────┬─────────┘
       │ HTTPS (cookie session)                                 │
       │                                                        │ get/put
       ▼                                                        │
┌──────────────────────────┐   enqueue    ┌────────────────────┴─────────┐
│      FastAPI (api)       │─────────────▶│    Redis (broker + cache)    │
│  auth · REST · SSE       │◀─────────────│                              │
└──────┬───────────────────┘   progress   └───────┬──────────────────────┘
       │                                          │ consume
       │ SQL                                      ▼
       │                          ┌───────────────────────────────────┐
       │                          │       Celery workers              │
       │                          │  ┌─────────────┬────────────────┐ │
       │                          │  │ ingest pool │ strava-sync    │ │
       │                          │  │ (CPU, N×)   │ pool (I/O, 1×) │ │
       │                          │  └─────────────┴────────────────┘ │
       │                          │       + beat (scheduler)          │
       ▼                          └───────────────┬───────────────────┘
┌──────────────────────────┐                      │
│      PostgreSQL 17       │◀─────────────────────┘
│  users · activities      │
│  aggregates · rollups    │
└──────────────────────────┘
       ▲
       │ webhook POST (activity create/update/delete)
┌──────┴───────┐
│  Strava API  │
└──────────────┘
```

Five processes total in v1: `api`, `worker-ingest`, `worker-sync`, `beat`, plus
Postgres/Redis/object-store. All defined in one `docker-compose.yml`.

---

## 3. Tech stack and why

### Backend: Python 3.12 + FastAPI

**Chosen because** the parsing ecosystem is the deciding factor. `fitdecode`,
`gpxpy`, `python-tcxreader`, `pandas`/`pyarrow`, and every sports-science reference
implementation are Python. Rewriting FIT decoding in Go or Node would be the single
largest source of bugs in the project for zero benefit.

FastAPI specifically over Django/Flask/Litestar:

- **OpenAPI is generated, not written.** The React client's types are generated from
  it. This kills an entire class of frontend/backend drift bugs and is worth a lot on
  a small team.
- Pydantic v2 validation at the boundary is the same library we use for the canonical
  parser models — one mental model, one place to define a shape.
- Native async matters for the endpoints that fan out to Strava and for SSE progress
  streams. Django would need channels/ASGI bolted on; we'd use ~10% of the framework.

*Rejected:* Django (ORM + admin are nice, but we don't want its request model or its
opinions about templates, and DRF serializers are a worse Pydantic). Node/TS backend
(would unify the language, but there is no credible FIT parser and no scientific
stack — the wrong trade). Go (fast, but we'd hand-write FIT decoding and lose pandas).

### Task queue: Celery 5 + Redis

**Chosen because** the ingest pipeline is literally a textbook fan-out/fan-in and
Celery's `group`/`chord`/`chain` primitives express it directly:

```python
chord(
    group(parse_chunk.s(upload_id, chunk) for chunk in chunks),
    finalize_import.s(upload_id),
)()
```

Plus: separate queues with separate worker pools (CPU-heavy ingest vs. rate-limited
network sync must not share a pool), mature retry/backoff, `celery beat` for
schedules, and Flower for ops visibility on day one.

*Rejected:* Dramatiq (nicer API, thinner ecosystem, weaker fan-in). ARQ (great for
pure-async I/O, poor fit for CPU-bound multiprocess work). RQ (no chords, no routing
worth the name). A Postgres-backed queue like `pgqueuer` (attractive for "one less
service", but we need Redis for caching and rate-limit token buckets anyway, so it
saves nothing). **Reconsider Temporal at v3** if the ingest workflow grows more than
~5 stateful steps — durable execution would then beat hand-rolled resume logic.

### Database: PostgreSQL 17

Single source of truth for everything relational. Specific features we're relying on:

- **`COPY ... FROM STDIN (FORMAT BINARY)`** via psycopg3 — bulk-loading 5,000 activity
  rows takes ~200ms versus ~30s of individual `INSERT`s. This is core to the fast path.
- **JSONB** for the `extra` catch-all and for per-sport variable metrics, with GIN
  indexes where we actually query into it.
- **Row-Level Security** for tenant isolation enforced below the application.
- **Materialized views** for the heavy rollups (weekly/monthly volume), refreshed
  concurrently at the end of an import.
- **Generated columns + partial indexes** for the sport-specific hot paths.

*Deferred, not rejected:* **PostGIS** — v1 stores routes as Google-encoded polylines
(a text column) and renders them client-side, which covers heatmaps and route display.
PostGIS earns its place at v2 when we add "activities near this point" and route
matching. **TimescaleDB** — see below; we don't put per-point data in Postgres at all,
so there's nothing for it to do.

### <a id="why-not-time-series-rows-in-postgres"></a>Why not time-series rows in Postgres

Run the numbers for one serious 10-year user:

```
3,000 activities × ~4,000 samples × 7 stream types ≈ 84,000,000 points
```

As narrow rows (`activity_id, t, value`) that's ~5–8 GB **per user** with indexes.
At 10,000 users, that's a 50–80 TB Postgres cluster whose only job is to answer
"draw this one activity's HR trace" — a query that touches one activity.

Instead: **one Parquet file per activity in object storage.**

```
streams/{user_id}/{activity_id}.parquet
```

Columnar, dictionary+delta encoded, zstd. A typical 1-hour ride with 7 channels is
**40–90 KB**. The whole 3,000-activity history is ~200 MB, costs ~$0.005/month on R2,
loads in one ranged GET, and is read with `pyarrow` in single-digit milliseconds.

Postgres then holds only what charts actually query: **activity summaries and
precomputed aggregates** (time-in-HR-zone as 5 integers, the power curve as 40
(duration, watts) pairs, splits, best efforts). Those are a few hundred bytes per
activity. A decade of history for one user is well under 5 MB in Postgres.

This is the highest-leverage decision in the document. It's also what makes DuckDB
viable later: at v2 we can run `SELECT ... FROM read_parquet('s3://…/{user}/*.parquet')`
for arbitrary cross-activity analysis with no new storage layer.

### Object storage: S3-compatible

MinIO in `docker-compose` for dev, **Cloudflare R2** in production (zero egress fees
— and we serve map tiles' worth of stream data straight to browsers), with S3 as the
drop-in alternative. Access exclusively through `packages/core/storage`, which speaks
`boto3` against an endpoint URL, so the three are interchangeable.

### Cache / broker: Redis 7

Celery broker + result backend, dashboard aggregate cache (keyed
`user:{id}:chart:{name}:{params_hash}`, invalidated by import completion), the
**global Strava rate-limit token bucket** (must be shared across all worker
processes — this is why it can't be in-process), and SSE progress pub/sub.

### Frontend: React 19 + TypeScript + Vite

- **Vite** — dev server startup and HMR are the whole argument; no reason to use
  anything else for an SPA in 2026.
- **TanStack Query** for server state: caching, background refetch, and the
  `isPending`/`isError` states that our "your import is still running" UX depends on.
- **TanStack Router** for typesafe routes + typed search params, so chart filters
  (`?range=1y&sport=run`) are URL state and every view is shareable/bookmarkable.
- **Tailwind CSS v4 + shadcn/ui** — we own the component source, no runtime CSS-in-JS
  cost, and theming is CSS custom properties which is exactly what the chart tokens need.
- **Charts: three libraries, deliberately** (justified in
  [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md#chart-system)): Recharts for standard
  dashboard charts, **uPlot** for the single-activity stream view (20k+ points at
  60fps — Recharts dies here), **MapLibre GL JS + deck.gl** for route maps and the
  all-time heatmap (no Mapbox token, no per-load billing).

*Rejected:* Next.js — this is an authenticated, highly-interactive SPA with no SEO
surface and no content to server-render. SSR would add a Node deployment target and
a data-fetching split for zero user-visible benefit. If marketing pages appear later,
they ship as a separate static site.

### Deployment

Dev: `docker compose up`. Prod v1: **Fly.io** or **Railway** — both run the
compose-shaped topology (5 process types, managed Postgres + Redis) without a
Kubernetes tax, and both give us a public HTTPS URL, which the Strava webhook
subscription *requires*. Kubernetes at the point where we need >20 worker instances
or multi-region, and not before.

---

## 4. Service responsibilities

### `api` (FastAPI, stateless, horizontally scalable)

| Route group | Responsibility |
|---|---|
| `/auth/strava/*` | OAuth start/callback, session cookie, token storage, deauthorize |
| `/uploads/*` | Mint presigned PUT, register upload, enqueue import, report status |
| `/imports/{id}` + `/imports/{id}/events` | Import status JSON + **SSE progress stream** |
| `/me/capabilities` | The capability set that drives which charts render (see CLAUDE.md §5) |
| `/activities` `/activities/{id}` `/activities/{id}/streams` | List, detail, presigned stream URL |
| `/charts/{chart_id}` | One endpoint per chart, returns chart-ready series + metadata |
| `/webhooks/strava` | `GET` subscription validation, `POST` event receipt (must return 200 in <2s) |

Hard rules: no handler does CPU work; no handler makes a blocking call to Strava; the
webhook handler validates, writes to `webhook_events`, enqueues, and returns.

### `worker-ingest` (CPU-bound, `--pool=prefork --concurrency=<cores>`)

Zip inspection, CSV fast path, FIT/GPX/TCX decoding, Parquet writing, metric
computation, rollup refresh. Scale by adding replicas — the fan-out is embarrassingly
parallel. This is the only pool that should ever be CPU-saturated.

### `worker-sync` (I/O-bound, `--pool=gevent`, **globally concurrency-limited**)

Every outbound Strava call. Deliberately a separate queue and pool because the rate
limit is per-*application*, shared across all users — see
[STRAVA_API.md](STRAVA_API.md#rate-limits). A single logical consumer pulling from a
Redis token bucket makes the limit enforceable; N independent pollers makes it
unenforceable.

### `beat`

Nightly: token refresh sweep, reconciliation sync (catch webhook misses), rollup
refresh, stale-upload cleanup, gear-mileage alerts.

---

## 5. How multi-tenancy and heterogeneous data are handled

This is the "user1 and user2 have different activities" requirement, made concrete.

**a) One canonical schema, many adapters.** Every input format — a row of
`activities.csv`, a `.fit` file, a `.gpx`, a `.tcx`, a Strava API JSON payload — is
converted by a registered parser into the same two Pydantic models,
`CanonicalActivity` and `CanonicalStreamSet`. Everything downstream (metrics,
storage, charts) sees only those. Adding Garmin or Wahoo export support later means
adding one adapter, changing nothing else.

**b) Sparse by design.** Every metric field on `CanonicalActivity` is `Optional`.
There is no default-to-zero anywhere — a missing heart rate is `None`, never `0`,
because `0` silently corrupts averages and makes charts lie. Metric functions
declare their required inputs and return `None` if unmet.

**c) Capability detection during ingest.** As files are parsed we accumulate, per
user: which sports appear, which stream channels exist, how many activities have
each, and over what date range. That lands in `user_capabilities` and drives which
charts the frontend renders. A power-meter chart never appears for a runner.

**d) Isolation in the database.** `user_id` on every user-scoped table + RLS policies
+ `SET LOCAL app.user_id` per request/task. Object-storage keys are prefixed by
`user_id` and every read is a scoped presigned URL.

**e) Per-user compute isolation.** Import tasks are routed with a per-user
concurrency cap so one person uploading a 10 GB archive cannot starve everyone else's
queue.

---

## 6. Scaling path

| Stage | Users | What changes |
|---|---|---|
| **v1** | 1–1k | Single compose stack. One Postgres, 2 ingest workers. Nothing is sharded. |
| **v2** | 1k–20k | Postgres read replica for dashboard queries. Ingest workers autoscale on queue depth. Redis cache for chart endpoints. Partition `activities` by `user_id` hash. |
| **v3** | 20k–200k | Separate the sync service out (it's the rate-limit bottleneck and needs its own scaling story — likely per-app-tier sharding across multiple Strava app registrations). DuckDB/ClickHouse for cross-user analytics. Object-store lifecycle policy: raw zips → cold storage after 90 days. |
| **v4** | 200k+ | Multi-region, per-region object store, Postgres sharded by user. At this point the ingest pipeline becomes a standalone service with its own API. |

The two things that would force an early rewrite, and are therefore designed for
now: **(1)** streams in Postgres — avoided; **(2)** rate-limit handling that assumes
one process — avoided via the shared Redis bucket.

---

## 7. Key risks

| Risk | Mitigation |
|---|---|
| Strava's per-app rate limit caps total users, regardless of our scaling | Webhooks (not polling) so steady-state cost is ~1 call/activity; bulk history comes from the user's export, not the API; monitor headroom as a first-class metric |
| Strava changes API terms or revokes access | The bulk-export path works with zero API access. The product degrades to manual re-upload rather than dying. |
| A 10 GB upload times out or the worker crashes mid-import | Presigned direct upload (no API timeout), per-file `ingest_items` status rows, resumable from last completed item |
| FIT files from exotic devices break the parser | Per-file failure isolation, `raw_payload` retained, golden-file fixture per bug |
| Metric formulas are wrong and nobody notices | Hand-computed expected values in tests with cited formulas; a `/debug/metrics/{activity_id}` view showing inputs and intermediates |
</content>
</invoke>
