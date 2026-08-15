# Ingestion — the bulk export pipeline

This is the hardest engineering problem in the product and the one that decides
whether the first-run experience feels magic or broken. Target: **a chart on screen
within 30 seconds of upload completing**, with full depth filled in behind it.

---

## 1. What's actually in the zip

The user gets it from **Strava → Settings → My Account → "Download or Delete Your
Account" → Request your archive**. Strava emails a download link, usually within a
few hours (they say up to a week). Size for a 10-year athlete: **500 MB – 10 GB**,
dominated by media and FIT files.

Structure (contents vary by account age — Strava has changed this format several
times, so **the parser must treat every file as optional**):

```
export_12345678/
├── activities.csv          ← THE FAST PATH. One row per activity, ~90 columns.
├── activities/
│   ├── 1234567890.fit.gz   ← usually gzipped
│   ├── 1234567891.gpx
│   ├── 1234567892.tcx.gz
│   └── … 500–5,000 files
├── profile.csv
├── measurements/           ← weight/HR history
├── bikes.csv, shoes.csv    ← gear (older exports), or gear in profile.csv
├── goals.csv
├── segments/, routes/
├── comments.csv, kudos.csv, followers.csv, following.csv
├── clubs/, media/          ← photos; we ignore these in v1 but must skip them cheaply
└── …
```

### `activities.csv` is the whole trick

It contains, per activity: Activity ID, Date, Name, Type, Description, Elapsed Time,
Distance, Max/Average Speed, Elevation Gain/Loss/Low/High, Max/Average Grade,
Average/Max Heart Rate, Average/Max Cadence, Average/Max Watts, Weighted Average
Power, Calories, Perceived Exertion, Commute flag, Gear, **and a `Filename` column
pointing at the corresponding file in `activities/`**.

That is 80–90% of every dashboard chart, in one text file, parseable in **under two
seconds** for 5,000 rows. The `.fit` files only add per-sample depth: HR-zone time,
power curves, splits, GPS traces.

**So the pipeline is two-phase.** Phase A gives the user a working dashboard almost
immediately; Phase B, which takes minutes, upgrades it in place.

Known gotchas, all of which need handling:

- Column headers differ between export vintages and localisations. Match by
  **normalised header name** (lowercase, strip units in parens), not by position.
- Numeric columns are locale-formatted in some exports (`1.234,5`). Sniff the decimal
  separator from the file.
- Some exports carry **two** distance columns (one in the header set, one appended)
  with different units — metres vs km. Prefer the one whose magnitude is consistent
  with elapsed time and speed.
- `Filename` may be blank (manual activity, no file) or point to a missing file.
- Dates are `MMM D, YYYY, H:MM:SS AM` in UTC, and the format shifts by locale.

---

## 2. Upload path

**Never POST a 10 GB file through the API.** It ties up a worker, breaks proxy body
limits, and has no resume.

```
1. Browser  → POST /uploads                    { filename, size }
2. API      → creates uploads row (awaiting_file)
              returns presigned S3 multipart URLs (8 MB parts) + upload_id
3. Browser  → PUT parts directly to object storage, with a progress bar
              retries individual failed parts; survives a dropped connection
4. Browser  → POST /uploads/{id}/complete      { parts: [{n, etag}] }
5. API      → completes multipart, sets status=queued, enqueues inspect_upload
6. Browser  → opens GET /imports/{id}/events   (SSE) and watches progress
```

Client-side pre-check before step 1: verify it's a zip and that `activities.csv`
exists in the central directory (readable from the last ~64 KB of the file via
`fetch` + `Range` on the local `File` object). If it's not a Strava export, tell the
user *before* they wait for a 4 GB upload.

---

## 3. The pipeline

```
inspect_upload
      │  read central directory only; classify members; write ingest_items
      ▼
fast_path_csv                       ◀── dashboard unlocks here (~10–30 s)
      │  parse activities.csv → COPY into activities (detail_level 0)
      │  parse profile / gear / measurements / goals
      │  compute initial capabilities + daily_load + rollups
      ▼
chord(
   group( parse_chunk × N )         ◀── the parallel deep parse
   ,
   finalize_import
)
```

### Stage 1 — `inspect_upload`

Open the zip **without extracting it**. Python's `zipfile` reads the central
directory from the tail of the file, so we can list 5,000 members and their sizes
from a couple of ranged GETs — no need to pull the whole archive down first (we use
`smart_open`/a seekable S3 file object so `zipfile` can do random access).

Classify each member into `ingest_items`:

- `activities.csv` → csv, high priority
- `activities/*.{fit,gpx,tcx}[.gz]` → parseable
- `media/*`, `clubs/*` → `skipped` immediately (never downloaded)
- everything else → `other`

Set `uploads.items_total` so the progress bar has a denominator on the first tick.

**Cheap sanity check:** if `activities.csv` is absent, fail fast with a specific,
actionable message ("This looks like a `.zip` but not a Strava export — make sure
you're uploading the archive Strava emailed you, not a folder you zipped yourself").

### Stage 2 — `fast_path_csv` (the whole point)

```python
def fast_path_csv(upload_id: UUID) -> None:
    df = read_activities_csv(zip_member_bytes("activities.csv"))  # pandas, ~1 s
    rows = [BulkCsvParser.to_canonical(r) for r in df.itertuples()]
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY activities (user_id, strava_activity_id, ...) FROM STDIN (FORMAT BINARY)"
        ) as cp,
    ):
        for r in rows:
            cp.write_row(to_tuple(r))
    upsert_capabilities(...)
    rebuild_daily_load(...)
    refresh_rollups(...)
    mark(upload_id, status="fast_path", fast_path_done_at=now())
    publish_sse(upload_id, {"phase": "dashboard_ready", "activities": len(rows)})
```

Binary `COPY` rather than `INSERT`: ~200 ms for 5,000 rows against ~30 s of
round-tripped inserts. We load into a `TEMP` table then `INSERT … ON CONFLICT DO
UPDATE` from it, so re-uploads merge instead of erroring.

**The user sees a full dashboard at this point** — volume, trends, PRs from summary
fields, calendar heatmap, gear mileage. A banner says deeper analysis is still
running.

### Stage 3 — `parse_chunk` (fan-out)

Files are grouped into chunks of **~50 members, balanced by total bytes**, not by
count — one 6-hour ultra FIT is worth fifty 20-minute commutes. Chunking amortises
task overhead (a Celery task round-trip is ~5–15 ms; a small FIT parse is ~150 ms, so
per-file tasks would spend meaningful time in the broker).

Per chunk, in the worker:

```
for member in chunk:
    bytes = read_member(zip, member)        # ranged read, gunzip if needed
    canonical, streams = PARSERS[kind].parse(bytes)
    write_parquet(f"streams/{user_id}/{activity_id}.parquet", streams)
    metrics = compute_all(canonical, streams, user_profile)
    accumulate(rows_to_upsert, zone_rows, best_effort_rows, pr_rows, split_rows)
    mark_item(ok, duration_ms)
# one batched COPY per table at chunk end, not per file
```

Ordering: **newest activities first.** People look at recent data. By the time they
scroll, the older half has landed.

Failure handling per file:

| Failure | Action |
|---|---|
| Corrupt/truncated FIT | `ingest_items.status='failed'` + error, continue |
| Unknown format | `skipped`, keep the bytes reference for later |
| Parses but no matching CSV row | Create the activity from the file (fuzzy dedupe, [DATA_MODEL §5](DATA_MODEL.md#5-dedupe-and-merge-rules)) |
| Worker OOM / kill | Chunk retried; already-`ok` items skipped on retry |

Chunk tasks are `acks_late=True` with `max_retries=3` and exponential backoff.
Because every item's status is recorded, a retry does the remaining work only.

### Stage 4 — `finalize_import` (fan-in)

1. Recompute `daily_load` and the CTL/ATL/TSB series over the full history.
2. Recompute all-time PRs and mark `is_all_time_best`.
3. Roll gear mileage up from activities.
4. `REFRESH MATERIALIZED VIEW CONCURRENTLY` on the volume views.
5. Recompute `user_capabilities` from the real stream inventory.
6. Invalidate the user's chart cache in Redis.
7. Set `status='complete'`, push a final SSE event, show a summary:
   *"3,412 activities imported · 2,987 with detailed data · 12 files couldn't be read
   ([see details](#))."*

---

## 4. Making it fast — the concrete levers

| Lever | Effect |
|---|---|
| **Two-phase (CSV first)** | Time-to-first-chart drops from ~15 min to ~20 s. Biggest single win, and it's a product win, not just a perf one. |
| **Ranged reads from object storage** | No full download, no local disk for the archive, no 10 GB temp file per worker. |
| **Byte-balanced chunking + prefork pool** | Near-linear speedup to core count. 3,000 FITs on 8 cores ≈ 4–7 min vs ~45 min serial. |
| **Binary `COPY`, batched per chunk** | 100×+ over row-wise inserts; the DB stops being the bottleneck. |
| **Skip media entirely at inspect time** | Media is often >60% of archive bytes and 0% of the value in v1. |
| **Parquet + zstd for streams** | ~90 KB/activity; writing is faster than the equivalent DB insert by an order of magnitude, and reads are a single GET. |
| **Newest-first ordering** | Perceived completion long before actual completion. |
| **Per-user concurrency cap** | One huge archive can't starve the queue for everyone else. |
| **Idempotent items table** | Retries are cheap; a crash costs one chunk, not one import. |

Rough budget for a 3,000-activity / 2.5 GB archive on 2 workers × 4 cores:

| Phase | Wall time |
|---|---|
| Upload (browser → R2, 50 Mbps up) | ~7 min (user-visible, has a progress bar) |
| Inspect | 5–15 s |
| **Fast path — dashboard live** | **10–25 s** |
| Deep parse | 4–8 min |
| Finalize | 20–40 s |

### If we need it faster later

- Move FIT decoding to a Rust extension (`pyo3`) — decoding is ~70% of deep-parse
  time. Expect 5–10×. Do this only if profiling confirms it; the pure-Python path is
  fine at v1 volumes.
- Have the *browser* parse `activities.csv` from the local file and POST the summary
  rows before the upload finishes. Time-to-first-chart becomes ~2 s. Nice trick,
  meaningful complexity, keep it for v2.

---

## 5. Merging the export with API data

The bulk export and the API overlap. Rules:

- The export is authoritative for **history before the account was connected**.
- The API is authoritative for **anything after**, and for any field it provides
  (source precedence in [DATA_MODEL §5](DATA_MODEL.md#5-dedupe-and-merge-rules)).
- On import completion, set `sync_state.last_activity_at` to the newest imported
  activity so the incremental sync knows exactly where to resume — and doesn't burn
  rate-limit budget re-fetching a decade.
- If a user uploads a *second, newer* export later, it's the same pipeline. Existing
  activities update in place; only new ones insert.

---

## 6. What the user sees while this runs

Failure to communicate here is what makes imports feel broken. Progress is pushed
over SSE and the UI is explicit at every step:

| Phase | Message |
|---|---|
| Uploading | `Uploading your archive… 412 MB of 2.4 GB` + cancel |
| Inspecting | `Reading your archive — found 3,412 activities` |
| Fast path done | `Your dashboard is ready. We're still analysing detailed data from 2,987 files — charts will get richer as it finishes.` |
| Deep parse | `Analysing… 1,204 / 2,987` + a live count of newly-unlocked charts |
| Complete | `All done. 3,412 activities · 12 files couldn't be read.` |
| Partial failure | Never a red error page. A neutral summary with an expandable list of the specific files and why. |

The user can close the tab; the import continues and an email/notification fires on
completion.
</content>
</invoke>
