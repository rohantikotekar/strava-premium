"""The bulk-export ingest pipeline (INGESTION.md).

    inspect_upload  ->  fast_path_csv  ->  chord(parse_chunk x N, finalize_import)
                            |
                            +-- dashboard goes live here, in ~20 s

Two properties everything else depends on:

* **Never fail the whole import for one bad file** (CLAUDE.md §4.6). Every member
  gets a row in `ingest_items`; a corrupt .fit is recorded and skipped.
* **All writes are idempotent** (CLAUDE.md §4.2). Re-uploading the same archive is
  a no-op, and a retried chunk redoes only its unfinished items.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from datetime import UTC, datetime
from uuid import UUID

import structlog
from celery import chord
from sp_core.canonical.activity import CanonicalActivity, Channel
from sp_core.canonical.profile import AthleteProfile
from sp_core.canonical.sports import sport_group
from sp_core.geo import polyline as polyline_codec
from sp_core.metrics import analyze_activity, summarise_streams
from sp_core.parsers import parse_activity_file
from sp_core.parsers.csv_index import BulkCsvParser
from sp_core.storage.objects import get_bytes, put_bytes, stream_key
from sp_core.storage.parquet import streams_to_parquet
from sp_db.models import (
    Activity,
    ActivityBestEffort,
    ActivityDistancePR,
    ActivityZoneTime,
    IngestItem,
    Upload,
    User,
)
from sp_db.session import sync_session_scope
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sp_worker.celery_app import celery_app

log = structlog.get_logger(__name__)

#: Members we never even download. Media is often >60% of an archive's bytes and
#: 0% of its analytical value in v1.
_SKIP_PREFIXES = ("media/", "clubs/", "photos/")
_SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov", ".gif")

#: Chunk by *bytes*, not by count — one 6-hour ultra is worth fifty commutes.
_CHUNK_TARGET_BYTES = 24 * 1024 * 1024
_CHUNK_MAX_FILES = 60


def _profile(user: User) -> AthleteProfile:
    return AthleteProfile(
        ftp_w=user.ftp_w,
        max_hr_bpm=user.max_hr_bpm,
        resting_hr_bpm=user.resting_hr_bpm,
        weight_kg=user.weight_kg,
        sex=user.sex,
    )


def _open_zip(object_key: str) -> zipfile.ZipFile:
    """Read the archive from object storage.

    v1 pulls the whole object into memory once per task. The seekable-ranged-read
    optimisation described in INGESTION.md §3 is the next step here; it matters at
    multi-GB archives and is a contained change to this function.
    """
    return zipfile.ZipFile(io.BytesIO(get_bytes(object_key)))


def _set_status(upload_id: str, **fields: object) -> None:
    with sync_session_scope(None) as session:
        session.execute(update(Upload).where(Upload.id == UUID(upload_id)).values(**fields))


@celery_app.task(name="sp_worker.ingest.inspect_upload", bind=True, max_retries=3)
def inspect_upload(self, upload_id: str, user_id: str) -> dict[str, int]:
    """Stage 1 — classify every member without extracting the archive."""
    started = time.monotonic()
    _set_status(upload_id, status="inspecting", started_at=datetime.now(UTC))

    with sync_session_scope(UUID(user_id)) as session:
        upload = session.get(Upload, UUID(upload_id))
        if upload is None:
            return {"items": 0}
        object_key = upload.object_key

    try:
        archive = _open_zip(object_key)
    except Exception as exc:
        log.error("ingest.inspect.failed", upload_id=upload_id, error=str(exc))
        _set_status(
            upload_id,
            status="failed",
            error="We couldn't open that file as a zip archive. Is it the file Strava emailed you?",
        )
        return {"items": 0}

    names = archive.namelist()
    csv_member = next((n for n in names if n.lower().endswith("activities.csv")), None)
    if csv_member is None:
        _set_status(
            upload_id,
            status="failed",
            error=(
                "We couldn't find activities.csv in this archive. Make sure you're uploading "
                "the export Strava emailed you, not a folder you zipped yourself."
            ),
        )
        return {"items": 0}

    rows: list[dict[str, object]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        lowered = info.filename.lower()
        skip = lowered.startswith(_SKIP_PREFIXES) or lowered.endswith(_SKIP_SUFFIXES)
        kind = "csv" if lowered.endswith(".csv") else _kind_for(lowered)
        rows.append(
            {
                "upload_id": UUID(upload_id),
                "user_id": UUID(user_id),
                "member_path": info.filename,
                "member_size": info.file_size,
                "kind": kind,
                "status": "skipped" if skip or kind is None else "pending",
            }
        )

    with sync_session_scope(UUID(user_id)) as session:
        # ON CONFLICT DO NOTHING makes a retried inspect harmless.
        for batch_start in range(0, len(rows), 1000):
            session.execute(
                pg_insert(IngestItem)
                .values(rows[batch_start : batch_start + 1000])
                .on_conflict_do_nothing(index_elements=["upload_id", "member_path"])
            )
        parseable = sum(1 for r in rows if r["status"] == "pending" and r["kind"] != "csv")
        session.execute(
            update(Upload)
            .where(Upload.id == UUID(upload_id))
            .values(items_total=parseable, status="fast_path")
        )

    log.info(
        "ingest.inspect.done",
        upload_id=upload_id,
        members=len(rows),
        parseable=parseable,
        ms=int((time.monotonic() - started) * 1000),
    )
    fast_path_csv.delay(upload_id, user_id, csv_member)
    return {"items": parseable}


def _kind_for(lowered: str) -> str | None:
    stripped = lowered.removesuffix(".gz")
    for extension in ("fit", "gpx", "tcx"):
        if stripped.endswith(f".{extension}"):
            return extension
    return None


@celery_app.task(name="sp_worker.ingest.fast_path_csv", bind=True, max_retries=3)
def fast_path_csv(self, upload_id: str, user_id: str, csv_member: str) -> dict[str, int]:
    """Stage 2 — the whole point of the two-phase design.

    Parses activities.csv and bulk-loads summary rows. The dashboard is usable the
    moment this returns, typically ~20 s in, rather than after the multi-minute
    deep parse.
    """
    started = time.monotonic()

    with sync_session_scope(UUID(user_id)) as session:
        upload = session.get(Upload, UUID(upload_id))
        user = session.get(User, UUID(user_id))
        if upload is None or user is None:
            return {"activities": 0}
        object_key = upload.object_key
        profile = _profile(user)

    archive = _open_zip(object_key)
    results = BulkCsvParser().parse(archive.read(csv_member))

    written = 0
    with sync_session_scope(UUID(user_id)) as session:
        for result in results:
            activity = result.activity
            metrics = analyze_activity(activity, None, profile)
            values = _activity_values(UUID(user_id), activity, metrics, detail_level=0)
            _upsert_activity(session, values, overwrite_streams=False)
            written += 1

        session.execute(
            update(Upload)
            .where(Upload.id == UUID(upload_id))
            .values(
                status="deep_parse",
                fast_path_done_at=datetime.now(UTC),
                activities_found=written,
            )
        )

    _rebuild_derived(user_id)

    log.info(
        "ingest.fast_path.done",
        upload_id=upload_id,
        activities=written,
        ms=int((time.monotonic() - started) * 1000),
    )

    dispatch_deep_parse.delay(upload_id, user_id)
    return {"activities": written}


@celery_app.task(name="sp_worker.ingest.dispatch_deep_parse")
def dispatch_deep_parse(upload_id: str, user_id: str) -> dict[str, int]:
    """Stage 3 — fan out the .fit/.gpx/.tcx files across workers.

    Newest first: people look at recent data, so perceived completion arrives long
    before actual completion.
    """
    with sync_session_scope(UUID(user_id)) as session:
        pending = (
            session.execute(
                select(IngestItem.member_path, IngestItem.member_size)
                .where(
                    IngestItem.upload_id == UUID(upload_id),
                    IngestItem.status == "pending",
                    IngestItem.kind.in_(("fit", "gpx", "tcx")),
                )
                .order_by(IngestItem.member_path.desc())
            )
        ).all()

    if not pending:
        finalize_import.delay([], upload_id, user_id)
        return {"chunks": 0}

    chunks: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for member_path, member_size in pending:
        current.append(member_path)
        current_bytes += int(member_size or 0)
        if current_bytes >= _CHUNK_TARGET_BYTES or len(current) >= _CHUNK_MAX_FILES:
            chunks.append(current)
            current, current_bytes = [], 0
    if current:
        chunks.append(current)

    log.info("ingest.dispatch", upload_id=upload_id, files=len(pending), chunks=len(chunks))
    chord(
        (parse_chunk.s(upload_id, user_id, chunk) for chunk in chunks),
        finalize_import.s(upload_id, user_id),
    ).apply_async()
    return {"chunks": len(chunks)}


@celery_app.task(
    name="sp_worker.ingest.parse_chunk", bind=True, max_retries=3, default_retry_delay=30
)
def parse_chunk(self, upload_id: str, user_id: str, members: list[str]) -> dict[str, int]:
    """Decode one chunk of activity files.

    Per-file failures are recorded and skipped; only an infrastructure failure
    retries the whole chunk, and already-`ok` items are not redone.
    """
    with sync_session_scope(UUID(user_id)) as session:
        upload = session.get(Upload, UUID(upload_id))
        user = session.get(User, UUID(user_id))
        if upload is None or user is None:
            return {"ok": 0, "failed": 0}
        object_key = upload.object_key
        profile = _profile(user)
        done_already = set(
            session.execute(
                select(IngestItem.member_path).where(
                    IngestItem.upload_id == UUID(upload_id),
                    IngestItem.member_path.in_(members),
                    IngestItem.status == "ok",
                )
            ).scalars()
        )

    todo = [m for m in members if m not in done_already]
    if not todo:
        return {"ok": 0, "failed": 0}

    archive = _open_zip(object_key)
    ok = failed = 0

    with sync_session_scope(UUID(user_id)) as session:
        for member in todo:
            item_started = time.monotonic()
            try:
                raw = archive.read(member)
                result = parse_activity_file(raw, member)
                activity = result.activity
                streams = result.streams

                # A GPX has no summary block; recover what we can from the samples.
                if streams is not None:
                    for field, value in summarise_streams(streams).items():
                        if getattr(activity, field, None) is None:
                            setattr(activity, field, value)

                activity.content_hash = hashlib.sha256(raw).hexdigest()
                activity.strava_activity_id = _strava_id_from_filename(member)

                metrics = analyze_activity(activity, streams, profile)
                values = _activity_values(UUID(user_id), activity, metrics, detail_level=1)

                if streams is not None and streams.n_samples:
                    lat, lng = streams.get(Channel.LAT), streams.get(Channel.LNG)
                    if lat is not None and lng is not None:
                        values["polyline"] = polyline_codec.from_streams(lat, lng)
                    values["has_streams"] = True
                    values["available_channels"] = metrics.available_channels

                activity_id = _upsert_activity(session, values, overwrite_streams=True)

                if streams is not None and streams.n_samples and activity_id is not None:
                    payload = streams_to_parquet(streams)
                    if payload:
                        key = stream_key(UUID(user_id), activity_id)
                        put_bytes(key, payload, "application/vnd.apache.parquet")
                        session.execute(
                            update(Activity)
                            .where(Activity.id == activity_id)
                            .values(stream_object_key=key, has_streams=True)
                        )

                if activity_id is not None:
                    _write_aggregates(session, UUID(user_id), activity_id, metrics)

                session.execute(
                    update(IngestItem)
                    .where(
                        IngestItem.upload_id == UUID(upload_id),
                        IngestItem.member_path == member,
                    )
                    .values(
                        status="ok",
                        activity_id=activity_id,
                        duration_ms=int((time.monotonic() - item_started) * 1000),
                        error=None,
                    )
                )
                ok += 1

            except Exception as exc:
                failed += 1
                session.execute(
                    update(IngestItem)
                    .where(
                        IngestItem.upload_id == UUID(upload_id),
                        IngestItem.member_path == member,
                    )
                    .values(status="failed", error=str(exc)[:500])
                )
                log.warning("ingest.item.failed", member=member, error=str(exc)[:200])

        session.execute(
            update(Upload)
            .where(Upload.id == UUID(upload_id))
            .values(
                items_done=Upload.items_done + ok,
                items_failed=Upload.items_failed + failed,
            )
        )

    log.info("ingest.chunk.done", upload_id=upload_id, ok=ok, failed=failed)
    return {"ok": ok, "failed": failed}


def _strava_id_from_filename(member: str) -> int | None:
    """Export files are named after the Strava activity id: ``activities/12345.fit.gz``."""
    stem = member.rsplit("/", 1)[-1].split(".")[0]
    return int(stem) if stem.isdigit() else None


@celery_app.task(name="sp_worker.ingest.finalize_import")
def finalize_import(
    chunk_results: list[dict[str, int]], upload_id: str, user_id: str
) -> dict[str, int]:
    """Stage 4 — recompute everything that depends on the whole history."""
    _set_status(upload_id, status="finalizing")
    _rebuild_derived(user_id)

    with sync_session_scope(UUID(user_id)) as session:
        counts = dict(
            session.execute(
                select(IngestItem.status, func.count())
                .where(IngestItem.upload_id == UUID(upload_id))
                .group_by(IngestItem.status)
            ).all()
        )
        total_activities = session.execute(
            select(func.count(Activity.id)).where(Activity.user_id == UUID(user_id))
        ).scalar_one()

        session.execute(
            update(Upload)
            .where(Upload.id == UUID(upload_id))
            .values(
                status="complete",
                completed_at=datetime.now(UTC),
                items_done=int(counts.get("ok", 0)),
                items_failed=int(counts.get("failed", 0)),
                activities_found=int(total_activities),
            )
        )

    log.info("ingest.finalize.done", upload_id=upload_id, activities=int(total_activities))
    return {"activities": int(total_activities), "failed": int(counts.get("failed", 0))}


# --------------------------------------------------------------------------- #
# Shared write helpers
# --------------------------------------------------------------------------- #


def _activity_values(
    user_id: UUID, activity: CanonicalActivity, metrics: object, detail_level: int
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "strava_activity_id": activity.strava_activity_id,
        "content_hash": activity.content_hash,
        "source": activity.source,
        "detail_level": detail_level,
        "start_time_utc": activity.start_time_utc,
        "start_time_local": activity.start_time_local,
        "utc_offset_s": activity.utc_offset_s,
        "elapsed_time_s": activity.elapsed_time_s,
        "moving_time_s": activity.moving_time_s,
        "sport_type": activity.sport_type,
        "sport_group": sport_group(activity.sport_type),
        "name": activity.name,
        "description": activity.description,
        "is_indoor": activity.is_indoor,
        "is_commute": activity.is_commute,
        "is_race": activity.is_race,
        "distance_m": activity.distance_m,
        "elevation_gain_m": activity.elevation_gain_m,
        "elevation_loss_m": activity.elevation_loss_m,
        "avg_speed_mps": activity.avg_speed_mps,
        "max_speed_mps": activity.max_speed_mps,
        "avg_hr_bpm": activity.avg_hr_bpm,
        "max_hr_bpm": activity.max_hr_bpm,
        "avg_cadence_rpm": activity.avg_cadence_rpm,
        "avg_power_w": activity.avg_power_w,
        "max_power_w": activity.max_power_w,
        "weighted_avg_power_w": activity.weighted_avg_power_w,
        "calories": activity.calories,
        "avg_temp_c": activity.avg_temp_c,
        "perceived_exertion": activity.perceived_exertion,
        "relative_effort": activity.relative_effort,
        "trimp": metrics.trimp,  # type: ignore[attr-defined]
        "tss": metrics.tss,  # type: ignore[attr-defined]
        "training_load": metrics.training_load,  # type: ignore[attr-defined]
        "load_source": metrics.load_source,  # type: ignore[attr-defined]
        "intensity_factor": metrics.intensity_factor,  # type: ignore[attr-defined]
        "efficiency_factor": metrics.efficiency_factor,  # type: ignore[attr-defined]
        "decoupling_pct": metrics.decoupling_pct,  # type: ignore[attr-defined]
        "normalized_power_w": metrics.normalized_power_w,  # type: ignore[attr-defined]
        "start_lat": activity.start_lat,
        "start_lng": activity.start_lng,
        "bbox": list(activity.bbox) if activity.bbox else None,
        "extra": activity.extra,
        "updated_at": datetime.now(UTC),
    }


def _upsert_activity(session, values: dict[str, object], overwrite_streams: bool) -> UUID | None:
    """Insert or merge on the natural key.

    Field-level precedence: a non-NULL value from a richer source wins, and a NULL
    never overwrites a real value (DATA_MODEL.md §5).
    """
    strava_id = values.get("strava_activity_id")
    content_hash = values.get("content_hash")

    existing = None
    if strava_id is not None:
        existing = session.execute(
            select(Activity).where(
                Activity.user_id == values["user_id"],
                Activity.strava_activity_id == strava_id,
            )
        ).scalar_one_or_none()
    if existing is None and content_hash:
        existing = session.execute(
            select(Activity).where(
                Activity.user_id == values["user_id"], Activity.content_hash == content_hash
            )
        ).scalar_one_or_none()

    if existing is None:
        row = Activity(**values)  # type: ignore[arg-type]
        session.add(row)
        session.flush()
        return row.id

    incoming_detail = int(values.get("detail_level") or 0)
    richer = incoming_detail >= int(existing.detail_level or 0)
    for field, value in values.items():
        if field in ("user_id", "id"):
            continue
        if value is None:
            continue  # never let a missing value clobber a real one
        if not richer and field not in ("updated_at",):
            continue
        setattr(existing, field, value)
    session.flush()
    return existing.id


def _write_aggregates(session, user_id: UUID, activity_id: UUID, metrics: object) -> None:
    """Replace this activity's precomputed aggregate rows. Idempotent by delete+insert."""
    for model in (ActivityZoneTime, ActivityBestEffort, ActivityDistancePR):
        session.query(model).filter(model.activity_id == activity_id).delete(
            synchronize_session=False
        )

    for kind, zones in metrics.zone_time.items():  # type: ignore[attr-defined]
        for zone_index, seconds in zones.items():
            session.add(
                ActivityZoneTime(
                    activity_id=activity_id,
                    user_id=user_id,
                    zone_kind=kind,
                    zone_index=zone_index,
                    seconds=seconds,
                )
            )

    for metric, curve in metrics.best_efforts.items():  # type: ignore[attr-defined]
        for duration_s, value in curve.items():
            session.add(
                ActivityBestEffort(
                    activity_id=activity_id,
                    user_id=user_id,
                    metric=metric,
                    duration_s=duration_s,
                    value=value,
                )
            )

    for distance_m, time_s in metrics.distance_prs.items():  # type: ignore[attr-defined]
        session.add(
            ActivityDistancePR(
                activity_id=activity_id,
                user_id=user_id,
                distance_m=distance_m,
                time_s=time_s,
            )
        )


def _rebuild_derived(user_id: str) -> None:
    from sp_worker.tasks.metrics import rebuild_all

    rebuild_all(user_id)
