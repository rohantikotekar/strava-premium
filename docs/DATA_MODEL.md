# Data model

Two stores, one rule: **Postgres holds anything a chart aggregates over; object
storage holds anything a chart draws point-by-point.**

Units are SI everywhere: metres, seconds, watts, joules, °C, kg. Conversion to
miles/feet/pace happens in the browser only.

---

## 1. The canonical contract

Every parser produces these. Nothing downstream knows what file format it came from.

```python
# packages/core/src/sp_core/canonical/activity.py


class CanonicalActivity(BaseModel):
    # identity ─────────────────────────────────────────────
    source: Literal["bulk_csv", "fit", "gpx", "tcx", "strava_api"]
    strava_activity_id: int | None  # primary dedupe key when present
    content_hash: str  # sha256 of source bytes; fallback key

    # when ─────────────────────────────────────────────────
    start_time_utc: datetime  # tz-aware, always UTC
    start_time_local: datetime  # naive wall-clock — needed for "morning runs"
    utc_offset_s: int
    elapsed_time_s: int
    moving_time_s: int | None

    # what ─────────────────────────────────────────────────
    sport_type: SportType  # normalised enum, see §5
    name: str | None
    description: str | None
    is_indoor: bool
    is_commute: bool
    is_race: bool
    gear_external_id: str | None

    # measures — EVERY ONE IS OPTIONAL. None means "not measured", never 0.
    distance_m: float | None
    elevation_gain_m: float | None
    elevation_loss_m: float | None
    avg_speed_mps: float | None
    max_speed_mps: float | None
    avg_hr_bpm: float | None
    max_hr_bpm: float | None
    avg_cadence_rpm: float | None
    avg_power_w: float | None
    max_power_w: float | None
    weighted_avg_power_w: float | None
    kilojoules: float | None
    calories: float | None
    avg_temp_c: float | None
    perceived_exertion: int | None  # 1–10, Strava "Perceived Exertion"
    relative_effort: float | None  # Strava's suffer score, if exported

    # geo ──────────────────────────────────────────────────
    start_lat: float | None
    start_lng: float | None
    polyline: str | None  # Google-encoded, simplified to ~500 pts
    bbox: tuple[float, float, float, float] | None

    extra: dict[str, Any] = {}  # anything unmapped — never discard


class StreamSet(BaseModel):
    """Per-sample channels. Written to Parquet, never to Postgres."""

    activity_ref: str
    n_samples: int
    channels: dict[ChannelName, np.ndarray]  # see §4 for the channel list
```

Parser registry (`packages/core/src/sp_core/parsers/__init__.py`) dispatches on
magic bytes first, extension second:

```python
PARSERS: dict[str, Parser] = {
    "fit": FitParser(),  # .fit, .fit.gz  — magic: bytes 8:12 == b".FIT"
    "gpx": GpxParser(),  # .gpx, .gpx.gz  — magic: b"<?xml" + "<gpx"
    "tcx": TcxParser(),  # .tcx, .tcx.gz
    "csv": BulkCsvParser(),  # activities.csv, measurements/*.csv
    "api": StravaApiParser(),
}
```

---

## 2. Postgres schema

### Identity & auth

Identity (email+password, Google OAuth, sessions, tokens) is decoupled from Strava —
Strava is a data connection a user makes *after* they have an account, not a login
method. Full schema (`users`, `auth_identities`, `strava_connections`, `sessions`,
`email_verification_tokens`, `password_reset_tokens`, `auth_events`) and every flow
that touches it live in **[AUTH.md](AUTH.md)** — that is the source of truth, not a
duplicate copy here. The fitness-profile columns on `users` (`weight_kg`, `ftp_w`,
`max_hr_bpm`, `resting_hr_bpm`, `measurement_pref`, `timezone`) are unchanged from
the original design; only the identity columns moved.

The one shape change that ripples into this document: `strava_athlete_id` now lives
on `strava_connections`, not on `users` — every reference below to "the Strava
athlete id" means that table.

### Ingest tracking

```sql
CREATE TYPE upload_status AS ENUM
    ('awaiting_file','queued','inspecting','fast_path','deep_parse','finalizing','complete','failed');

CREATE TABLE uploads (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    object_key          TEXT NOT NULL,       -- raw/{user_id}/{upload_id}/export.zip
    filename            TEXT,
    size_bytes          BIGINT,
    sha256              TEXT,
    status              upload_status NOT NULL DEFAULT 'awaiting_file',
    -- progress, surfaced over SSE
    items_total         INTEGER DEFAULT 0,
    items_done          INTEGER DEFAULT 0,
    items_failed        INTEGER DEFAULT 0,
    error               TEXT,
    started_at          TIMESTAMPTZ,
    fast_path_done_at   TIMESTAMPTZ,         -- the moment the dashboard unlocks
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per file inside the zip. This table is what makes an import resumable
-- and what lets one corrupt .fit fail without killing the other 2,999.
CREATE TABLE ingest_items (
    id                  BIGSERIAL PRIMARY KEY,
    upload_id           UUID NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    member_path         TEXT NOT NULL,       -- path inside the zip
    member_size         BIGINT,
    kind                TEXT,                -- fit | gpx | tcx | csv | media | other
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|skipped|failed
    activity_id         UUID REFERENCES activities(id) ON DELETE SET NULL,
    error               TEXT,
    duration_ms         INTEGER,
    UNIQUE (upload_id, member_path)
);
CREATE INDEX ON ingest_items (upload_id, status);
```

### Activities — the centre of the model

```sql
CREATE TABLE activities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    strava_activity_id  BIGINT,
    content_hash        TEXT,
    source              TEXT NOT NULL,       -- bulk_csv | fit | gpx | tcx | strava_api
    detail_level        SMALLINT NOT NULL DEFAULT 0,  -- 0=summary only, 1=streams parsed

    start_time_utc      TIMESTAMPTZ NOT NULL,
    start_time_local    TIMESTAMP    NOT NULL,
    utc_offset_s        INTEGER      NOT NULL DEFAULT 0,
    elapsed_time_s      INTEGER      NOT NULL,
    moving_time_s       INTEGER,

    sport_type          TEXT NOT NULL,
    sport_group         TEXT NOT NULL,       -- run | ride | swim | ski | walk | other
    name                TEXT,
    description         TEXT,
    is_indoor           BOOLEAN NOT NULL DEFAULT false,
    is_commute          BOOLEAN NOT NULL DEFAULT false,
    is_race             BOOLEAN NOT NULL DEFAULT false,
    gear_id             UUID REFERENCES gear(id) ON DELETE SET NULL,

    distance_m          DOUBLE PRECISION,
    elevation_gain_m    DOUBLE PRECISION,
    elevation_loss_m    DOUBLE PRECISION,
    avg_speed_mps       DOUBLE PRECISION,
    max_speed_mps       DOUBLE PRECISION,
    avg_hr_bpm          REAL,
    max_hr_bpm          REAL,
    avg_cadence_rpm     REAL,
    avg_power_w         REAL,
    max_power_w         REAL,
    weighted_avg_power_w REAL,
    kilojoules          REAL,
    calories            REAL,
    avg_temp_c          REAL,
    perceived_exertion  SMALLINT,

    -- derived (computed by us, not Strava)
    trimp               REAL,                -- HR-based training load
    tss                 REAL,                -- power-based training load
    training_load       REAL,                -- best available: tss ?? trimp ?? rpe-load
    intensity_factor    REAL,
    efficiency_factor   REAL,
    decoupling_pct      REAL,                -- aerobic decoupling, 1st vs 2nd half
    grade_adj_pace_spm  REAL,                -- GAP, s/m

    start_lat           DOUBLE PRECISION,
    start_lng           DOUBLE PRECISION,
    polyline            TEXT,
    bbox                BOX,

    stream_object_key   TEXT,                -- streams/{user_id}/{activity_id}.parquet
    has_streams         BOOLEAN NOT NULL DEFAULT false,
    available_channels  TEXT[] NOT NULL DEFAULT '{}',

    extra               JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedupe keys. Partial-unique so rows without a Strava id are still constrained.
CREATE UNIQUE INDEX activities_strava_uq  ON activities (user_id, strava_activity_id)
    WHERE strava_activity_id IS NOT NULL;
CREATE UNIQUE INDEX activities_hash_uq    ON activities (user_id, content_hash)
    WHERE strava_activity_id IS NULL AND content_hash IS NOT NULL;

-- The workhorse index: every chart is "this user, this sport, this date range".
CREATE INDEX activities_user_time  ON activities (user_id, start_time_utc DESC);
CREATE INDEX activities_user_sport_time ON activities (user_id, sport_group, start_time_utc DESC);
CREATE INDEX activities_extra_gin  ON activities USING GIN (extra jsonb_path_ops);
```

`detail_level` is important: a row created from `activities.csv` in the fast path is
level 0 and already chartable. The deep parse upgrades it to level 1 in place. The UI
can show "getting deeper detail…" without blocking.

### Precomputed aggregates — what charts actually read

```sql
-- Time in each HR/power/pace zone. 5 rows per activity max, not 4,000.
CREATE TABLE activity_zone_time (
    activity_id   UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL,
    zone_kind     TEXT NOT NULL,      -- hr | power | pace
    zone_index    SMALLINT NOT NULL,  -- 1..5 (or 1..7 for power)
    seconds       INTEGER NOT NULL,
    PRIMARY KEY (activity_id, zone_kind, zone_index)
);

-- Mean-maximal curve: best sustained value for each duration bucket.
-- ~40 rows/activity. Powers both the power curve and the pace curve.
CREATE TABLE activity_best_efforts (
    activity_id   UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL,
    metric        TEXT NOT NULL,      -- power | pace | hr
    duration_s    INTEGER NOT NULL,   -- 1,5,15,30,60,300,600,1200,3600,...
    value         REAL NOT NULL,
    PRIMARY KEY (activity_id, metric, duration_s)
);
CREATE INDEX ON activity_best_efforts (user_id, metric, duration_s, value DESC);

-- Distance PRs: fastest 1k/5k/10k/HM/M etc. within an activity.
CREATE TABLE activity_distance_prs (
    activity_id   UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL,
    distance_m    INTEGER NOT NULL,   -- 400,1000,1609,5000,10000,21097,42195
    time_s        INTEGER NOT NULL,
    is_all_time_best BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (activity_id, distance_m)
);

CREATE TABLE activity_splits (
    activity_id   UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    split_index   SMALLINT NOT NULL,
    distance_m    DOUBLE PRECISION,
    elapsed_s     INTEGER,
    elev_gain_m   REAL,
    avg_hr_bpm    REAL,
    avg_power_w   REAL,
    PRIMARY KEY (activity_id, split_index)
);
```

### Daily fitness model (CTL / ATL / TSB)

```sql
-- One row per user per calendar day, including rest days (load = 0).
-- Rest days MUST exist as rows or the exponential decay is wrong.
CREATE TABLE daily_load (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day           DATE NOT NULL,
    load          REAL NOT NULL DEFAULT 0,     -- summed training_load
    duration_s    INTEGER NOT NULL DEFAULT 0,
    distance_m    DOUBLE PRECISION NOT NULL DEFAULT 0,
    activity_count SMALLINT NOT NULL DEFAULT 0,
    ctl           REAL,                        -- fitness, 42d EWMA
    atl           REAL,                        -- fatigue, 7d EWMA
    tsb           REAL,                        -- form = ctl - atl
    PRIMARY KEY (user_id, day)
);
```

### Gear, goals, capabilities, sync

```sql
CREATE TABLE gear (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    external_id     TEXT,                    -- Strava gear id, e.g. "g1234"
    kind            TEXT NOT NULL,           -- shoe | bike | other
    name            TEXT NOT NULL,
    brand           TEXT, model TEXT,
    retired         BOOLEAN NOT NULL DEFAULT false,
    distance_m      DOUBLE PRECISION NOT NULL DEFAULT 0,   -- rolled up from activities
    alert_at_m      DOUBLE PRECISION,        -- user-set replacement threshold
    UNIQUE (user_id, external_id)
);

CREATE TABLE body_measurements (          -- from export measurements/*.csv, or manual
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day       DATE NOT NULL,
    weight_kg NUMERIC(5,2),
    resting_hr SMALLINT,
    PRIMARY KEY (user_id, day)
);

-- Drives which charts the frontend renders. See CLAUDE.md §5.
CREATE TABLE user_capabilities (
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capability     TEXT NOT NULL,     -- 'stream.heartrate' | 'sport.ride' | 'field.power'
    activity_count INTEGER NOT NULL,
    first_seen     DATE,
    last_seen      DATE,
    PRIMARY KEY (user_id, capability)
);

CREATE TABLE sync_state (
    user_id           UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_synced_at    TIMESTAMPTZ,
    last_activity_at  TIMESTAMPTZ,       -- watermark for incremental pulls
    webhook_ok        BOOLEAN NOT NULL DEFAULT false,
    consecutive_errors SMALLINT NOT NULL DEFAULT 0
);

-- Webhook events are recorded then processed async. The endpoint must return
-- 200 within 2 seconds or Strava retries and eventually disables the subscription.
CREATE TABLE webhook_events (
    id            BIGSERIAL PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id      BIGINT NOT NULL,       -- strava athlete id
    object_type   TEXT NOT NULL,
    object_id     BIGINT NOT NULL,
    aspect_type   TEXT NOT NULL,         -- create | update | delete
    updates       JSONB,
    processed_at  TIMESTAMPTZ,
    error         TEXT
);
CREATE INDEX ON webhook_events (processed_at) WHERE processed_at IS NULL;
```

### Rollups (materialized)

```sql
CREATE MATERIALIZED VIEW mv_weekly_volume AS
SELECT user_id,
       sport_group,
       date_trunc('week', start_time_local)::date AS week,
       count(*)                     AS activities,
       sum(distance_m)              AS distance_m,
       sum(moving_time_s)           AS moving_time_s,
       sum(elevation_gain_m)        AS elevation_gain_m,
       sum(training_load)           AS training_load
FROM activities
GROUP BY 1,2,3;
CREATE UNIQUE INDEX ON mv_weekly_volume (user_id, sport_group, week);
-- REFRESH MATERIALIZED VIEW CONCURRENTLY at the end of every import and nightly.
```

`mv_monthly_volume` and `mv_yearly_volume` follow the same shape.

### Row-level security

Applied to every user-scoped table:

```sql
ALTER TABLE activities ENABLE ROW LEVEL SECURITY;
CREATE POLICY activities_tenant ON activities
    USING (user_id = current_setting('app.user_id', true)::uuid);
```

The API sets `SET LOCAL app.user_id = :uid` at the start of each request
transaction; workers do the same per task. A migration adding a user-scoped table
without a policy fails CI.

---

## 3. Sport taxonomy

Strava has ~50 `sport_type` values across export CSV and API, spelled
inconsistently between them (`Ride` vs `ride` vs `VirtualRide`). We keep the raw
value **and** map to a `sport_group` used for all grouping and charting:

| `sport_group` | Includes |
|---|---|
| `run` | Run, TrailRun, VirtualRun, Treadmill |
| `ride` | Ride, MountainBikeRide, GravelRide, VirtualRide, EBikeRide, Velomobile |
| `swim` | Swim (pool + open water) |
| `walk` | Walk, Hike, Snowshoe |
| `ski` | AlpineSki, BackcountrySki, NordicSki, Snowboard |
| `water` | Kayaking, Canoeing, Rowing, Surfing, StandUpPaddling, Kitesurf, Windsurf |
| `gym` | WeightTraining, Workout, Crossfit, Yoga, Pilates, Elliptical, StairStepper |
| `other` | everything unmapped — **never dropped**, always visible in the UI |

The mapping table lives in `packages/core/src/sp_core/canonical/sports.py`. Unknown
values fall through to `other` and are logged so we can add them.

---

## 4. Object storage layout

```
s3://strava-premium/
├── raw/{user_id}/{upload_id}/export.zip            # immutable, the source of truth
├── streams/{user_id}/{activity_id}.parquet         # per-sample channels
├── exports/{user_id}/{job_id}.csv                  # user-requested data exports
└── tmp/{upload_id}/…                               # 24h lifecycle rule
```

**Stream Parquet schema** — one column per channel, only channels that exist:

| Column | Type | Notes |
|---|---|---|
| `t` | `int32` | seconds from activity start |
| `lat`, `lng` | `float64` | omitted for indoor |
| `altitude_m` | `float32` | |
| `distance_m` | `float32` | cumulative |
| `speed_mps` | `float32` | |
| `heartrate_bpm` | `uint8` | |
| `cadence_rpm` | `uint8` | |
| `power_w` | `uint16` | |
| `temp_c` | `int8` | |
| `grade_pct` | `float32` | derived |
| `moving` | `bool` | |

Written with `pyarrow`, `compression="zstd"`, `use_dictionary=False` for numerics.
Row-group size 8192 so a partial read of a long activity is cheap.

---

## 5. Dedupe and merge rules

An activity can arrive up to three times: the CSV index row, the FIT file, and later
the API. Resolution order:

1. **Match on `(user_id, strava_activity_id)`** when present. The export CSV's
   `Activity ID` column and the API's `id` are the same number — this covers ~99%.
2. **Fallback fuzzy match** for orphan files (a `.gpx` with no CSV row): same user,
   `start_time_utc` within ±90s, and `distance_m` within ±2%. 90 seconds because
   device clocks drift and Strava's start time is the upload's, not the device's.
3. **Field-level merge, best-source-wins.** Per field precedence:
   `strava_api > fit > tcx > gpx > bulk_csv`. A non-`None` value from a
   higher-precedence source overwrites; a `None` never overwrites a real value.
   Recorded in `extra.field_sources` so we can debug where a number came from.
</content>
</invoke>
