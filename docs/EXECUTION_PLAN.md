# Execution plan

## Strategy

**Build the thinnest possible end-to-end slice first, then deepen it.**

The instinct on a project like this is to build the parser properly, then the schema
properly, then the API, then finally a UI — and discover in week 8 that the whole
thing hinges on an assumption about `activities.csv` that was wrong in week 1.

So: **M1 is a vertical slice that goes from a real zip to a real chart in a browser**,
with one file format, one chart, and no polish. Every milestone after that widens the
slice. There is a demoable, deployed product at the end of every milestone.

Estimates assume **1–2 engineers**. Halve the calendar with two; the sequencing
doesn't change because the dependencies are real.

---

## Milestone map

```
M0  Foundations          ~3 days   repo, compose, CI, migrations
M1  Vertical slice       ~1 week   zip → activities.csv → one chart      ◀ FIRST DEMO
M2  Real ingest          ~2 weeks  FIT/GPX/TCX, parallel, streams, Parquet
M3  Accounts & Strava    ~1.5 wks  signup/login (password + Google), OAuth, webhooks
M4  Analytics core       ~2 weeks  fitness model, curves, zones, PRs
M5  The real UI          ~2 weeks  full dashboard, activity detail, maps
M6  Hardening            ~1 week   edge cases, errors, a11y, perf
M7  Launch               ~1 week   deploy, limits, monitoring, docs
                        ─────────
                         ~11 weeks to a public v1
```

---

## M0 — Foundations (~3 days)

Boring, and skipping it costs a week later.

- [ ] uv workspace: `apps/api`, `apps/worker`, `packages/core`, `packages/db`
- [ ] pnpm workspace: `apps/web` (Vite + React 19 + TS strict + Tailwind v4 + shadcn)
- [ ] `docker-compose.yml`: postgres:17, redis:7, minio, api, worker, beat
- [ ] Alembic wired up; first migration = `users` + `activities` skeleton + RLS helper
- [ ] Ruff + mypy + Biome configs; pre-commit hooks
- [ ] GitHub Actions: lint → typecheck → test, on every PR, required to merge
- [ ] `Makefile` / `justfile`: `dev`, `test`, `migrate`, `gen:api`, `seed`
- [ ] **Fixture corpus**: obtain a real Strava export (your own), scrub coordinates
      and dates, commit 15–20 representative files to `tests/fixtures/`

**DoD:** `make dev` brings up the stack; `make test` passes; CI is green on a trivial PR.

> **Start in parallel, day 1:** register the Strava app and request a raised athlete
> limit. It's human-reviewed and it is the most likely thing to block launch.

---

## M1 — Vertical slice (~1 week) ◀ first demo

**Goal: upload a real 10-year export and see a real chart.** Everything hardcoded
that can be. No OAuth, no FIT parsing, no styling beyond defaults.

- [ ] `POST /uploads` → presigned single-part PUT (multipart comes in M2)
- [ ] Browser upload with a progress bar
- [ ] `inspect_upload` task: read zip central directory, find `activities.csv`
- [ ] `BulkCsvParser` → `CanonicalActivity` — **header-normalised, not positional**
- [ ] Binary `COPY` into `activities`
- [ ] `GET /charts/weekly-volume` → `{series, meta}`
- [ ] One Recharts column chart on a page, with the design tokens in place
- [ ] Hardcoded dev user; no auth

**DoD:** Drop in your own 10-year export → weekly volume chart for a decade appears
in under 60 seconds. **Demo it to a runner who isn't on the team and watch them use it.**

**What M1 is really testing:** whether `activities.csv` is as reliable as we think.
If the header/locale/unit assumptions in
[INGESTION §1](INGESTION.md#activitiescsv-is-the-whole-trick) are wrong, we find out
in week 1, not week 6.

---

## M2 — Real ingest (~2 weeks)

Turn the toy pipeline into the one described in [INGESTION.md](INGESTION.md).

- [ ] Multipart presigned upload with per-part retry and resume
- [ ] `ingest_items`: one row per member, status tracking, resumability
- [ ] `FitParser` (fitdecode), `GpxParser` (gpxpy), `TcxParser` — all to `StreamSet`
- [ ] Gzip member handling; magic-byte format detection
- [ ] Parquet writer + object-store layout
- [ ] Celery `chord`: byte-balanced chunking, newest-first ordering
- [ ] Per-file failure isolation + the failed-files report
- [ ] Dedupe & merge rules ([DATA_MODEL §5](DATA_MODEL.md#5-dedupe-and-merge-rules))
- [ ] `user_capabilities` population
- [ ] SSE progress endpoint + the live import UI
- [ ] Idempotency test: import the same zip twice, assert zero duplicates

**DoD:** A 3,000-activity export fully imports. Dashboard is live in <30 s
(fast path). Deep parse completes in <10 min on 8 cores. Killing a worker mid-import
and restarting resumes without duplicating or losing data.

**Highest-risk milestone.** Budget for FIT files from devices that violate the spec.
Every bug here gets a fixture.

---

## M3 — Accounts & Strava connection (~1.5 weeks)

Identity ([AUTH.md](AUTH.md)) and the Strava data connection
([STRAVA_API.md](STRAVA_API.md)) are separate systems that both land here — build
accounts first, since Strava connection now depends on an authenticated session.

**Accounts** (~0.5 week)
- [ ] `users`, `auth_identities`, `sessions`, `email_verification_tokens`,
      `password_reset_tokens`, `auth_events` migrations
- [ ] Signup/login with email+password — Argon2id hashing, HIBP breach check
- [ ] Email verification + password reset flows (token generation, expiry, single-use)
- [ ] Google OAuth (OIDC): start/callback, `id_token` verification, the
      link-vs-create resolution rules, explicit link/unlink from settings
- [ ] Opaque session cookies, `/me/sessions` list + revoke, revoke-on-reset
- [ ] Progressive rate limiting on login/signup (Redis-backed) + enumeration-safe responses
- [ ] Real multi-tenancy: RLS policies on every table, `SET LOCAL app.user_id`
- [ ] Remove the hardcoded dev user; every query is now tenant-scoped

**Strava connection** (~1 week)
- [ ] `strava_connections` table; OAuth connect flow for an already-authenticated user
- [ ] Token refresh with rotation handling + per-user Redis lock
- [ ] `StravaRateLimiter`: shared Redis token bucket, header-driven, priority lanes
- [ ] Webhook subscription management + `GET` validation + `POST` handler (<2 s)
- [ ] Webhook event processing: create / update / delete
- [ ] Nightly reconciliation using `after={watermark}`
- [ ] Deauthorization → delete Strava-sourced data + `strava_connections` row (not
      the account) — a separate "delete my account" path removes the login too

**DoD:** A user can sign up with email+password or Google, log in and out, reset a
forgotten password, and see only their own data (verified by a test asserting RLS
blocks cross-tenant reads) — all without ever touching Strava. Connecting Strava
from settings, then recording an activity on a phone, makes it appear in the app
within a minute without polling. Disconnecting Strava removes Strava data but the
account and login still work.

---

## M4 — Analytics core (~2 weeks)

Pure functions in `packages/core/metrics`, each with hand-computed test values.

- [ ] TRIMP, Normalized Power, IF, TSS, and the training-load fallback ladder
- [ ] `daily_load` builder — **including zero rows for rest days**
- [ ] CTL / ATL / TSB
- [ ] Mean-maximal curve (O(n) sliding window) → `activity_best_efforts`
- [ ] Distance PRs + all-time flagging → `activity_distance_prs`
- [ ] HR/power/pace zone time → `activity_zone_time`
- [ ] Splits, GAP, aerobic decoupling, efficiency factor
- [ ] Gear mileage rollup
- [ ] Materialized volume views + concurrent refresh
- [ ] `GET /charts/{id}` for every v1 chart in [FEATURES.md](FEATURES.md)
- [ ] Recompute-on-settings-change job (FTP / max HR)

**DoD:** Every v1 chart has a working endpoint returning correctly-shaped data.
Metrics validated against a known-good third-party tool on 5 real activities —
document the deltas and why any exist.

---

## M5 — The real UI (~2 weeks)

Build to [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md).

- [ ] Design tokens, light + dark, both validated
- [ ] `<ChartCard>` wrapper: loading / empty / error / table view / label enforcement
- [ ] Chart registry driven by `/me/capabilities`
- [ ] Dashboard: hero + KPI row + fitness chart + calendar + volume + recent
- [ ] Activity list: filter, search, infinite scroll, URL state
- [ ] Activity detail: MapLibre route + uPlot synced panels + tabs
- [ ] Progress page: PRs, curves, zones, year-over-year
- [ ] Heatmap page (deck.gl)
- [ ] Gear page
- [ ] Settings: zones, units, goals, import history, data export, delete account
- [ ] Onboarding + import wizard
- [ ] Glossary popovers everywhere jargon appears

**DoD:** A user can go OAuth → import → explore every v1 feature without hitting a
dead end, a raw error, or an unexplained empty box.

---

## M6 — Hardening (~1 week)

- [ ] Walk the entire [edge-case table](FRONTEND_DESIGN.md#edge-cases--the-ones-that-actually-happen)
      and verify each one by forcing the state
- [ ] Synthetic test users: no-HR, no-GPS, single-sport, 20-activity, 8,000-activity,
      indoor-only, multi-sport — as a seeded fixture set
- [ ] Accessibility pass: keyboard, screen reader, contrast, reduced motion
- [ ] Perf: dashboard TTI <2 s, chart endpoints p95 <300 ms, activity detail <1 s
- [ ] Load test: 20 concurrent imports; confirm the per-user cap holds
- [ ] Structured logging, Sentry, OpenTelemetry traces on the ingest pipeline
- [ ] Security review: RLS coverage test, presigned URL scoping, token encryption,
      log redaction, dependency audit

---

## M7 — Launch (~1 week)

- [ ] Deploy to Fly.io/Railway: api ×2, worker-ingest ×2, worker-sync ×1, beat ×1
- [ ] Managed Postgres with PITR; R2 bucket with lifecycle rules
- [ ] Production Strava app; **confirm the raised athlete limit landed**
- [ ] Production webhook subscription + an alert if it goes stale
- [ ] Dashboards: queue depth, import success rate, **Strava rate-limit headroom**,
      p95 latencies, error rate
- [ ] Alerts: import failure rate >5%, webhook 5xx, rate-limit >80%, queue backlog
- [ ] Backup + restore drill (actually restore, don't just configure it)
- [ ] Privacy policy, terms, Strava attribution and branding compliance
- [ ] `README` quickstart verified from a clean clone on a clean machine
- [ ] Beta with 10–20 real athletes, chosen for *different* data profiles

---

## Cut lines

If time runs short, cut in this order. Everything above the line ships.

| Cut first | Why it's safe |
|---|---|
| Heatmap page (deck.gl) | Cool, not load-bearing. Route maps on activity detail stay. |
| Pace curve (#15) | Power curve covers cyclists; runners still get PRs |
| Goals (#25) | Nice, not why anyone signs up |
| Dark mode | Ship light-only if the token work slips; the structure is already there |
| TCX parser | Rare in modern exports; log as skipped and add later |
| Data export | Post-launch, but promise it |

**Never cut:** the fast path (it *is* the first-run experience), capability-driven
rendering (without it the app breaks for most users), per-file failure isolation
(without it one bad file kills an import), RLS (a data leak ends the project).

---

## Definition of done — every milestone

1. Merged to `main` behind a flag if incomplete; `main` stays deployable
2. Tests written *with* the code, including a fixture for every parser bug fixed
3. CI green: ruff, mypy, pytest, biome, tsc, vitest
4. Deployed to staging and manually exercised
5. Docs in `docs/` updated in the same PR when a decision changed
6. Demoed to someone outside the team

---

## Immediate next actions

1. **Today:** register the Strava API app; request the athlete-limit increase.
2. **Today:** request your own bulk export from Strava — it takes hours to days to
   arrive and M1 is blocked without it.
3. **Day 1:** M0 scaffolding.
4. **Day 2:** the moment the export lands, open `activities.csv` and check every
   assumption in [INGESTION §1](INGESTION.md#1-whats-actually-in-the-zip) against
   the real thing. Correct the doc before writing the parser.

---

## Open questions to resolve before M4

| Question | Default if unanswered |
|---|---|
| Do we support multiple athletes per install (SaaS) or single-user self-hosted first? | Build multi-tenant from M3 — retrofitting RLS is far worse than not needing it |
| Which historical weather provider for v2? | Open-Meteo (free, no key, has a historical archive) |
| Is there any paid tier for *our* product? | Assume no for v1; nothing in the architecture depends on it |
| Hosted SaaS or self-host-first distribution? | Ship compose files either way; decide at M7 |
</content>
</invoke>
