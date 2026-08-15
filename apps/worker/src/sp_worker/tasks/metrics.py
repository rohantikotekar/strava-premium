"""Whole-history recomputation.

Anything that depends on more than one activity lives here: the daily load
calendar, the fitness curve, all-time PR flags, gear mileage, and the capability
set that decides which charts render.

Called at the end of an import and whenever the athlete's FTP or HR thresholds
change — when a formula's inputs change we recompute, we don't migrate stored
numbers (CLAUDE.md §4.3).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

import structlog
from sp_core.canonical.activity import CanonicalActivity
from sp_core.canonical.profile import AthleteProfile
from sp_core.metrics import analyze_activity, capability_for_channel
from sp_core.metrics.fitness import build_daily_series, compute_fitness_series
from sp_core.storage.objects import get_bytes
from sp_core.storage.parquet import parquet_to_streams
from sp_db.models import (
    Activity,
    ActivityDistancePR,
    Gear,
    User,
    UserCapability,
)
from sp_db.session import sync_session_scope
from sqlalchemy import delete, func, select, update

from sp_worker.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="sp_worker.metrics.recompute_user")
def recompute_user(user_id: str) -> dict[str, int]:
    """Entry point for 'the athlete changed their FTP / max HR'.

    Re-derives every per-activity metric from the stored streams, then rebuilds
    everything downstream. When a formula's inputs change we recompute; we never
    migrate the stored numbers (CLAUDE.md §4.3).
    """
    changed = recompute_activities(user_id)
    rebuild_all(user_id)
    return {"activities": changed}


def rebuild_all(user_id: str) -> dict[str, int]:
    rebuild_daily_load(user_id)
    rebuild_all_time_prs(user_id)
    rebuild_gear(user_id)
    rebuild_capabilities(user_id)
    return {"ok": 1}


def recompute_activities(user_id: str) -> int:
    """Re-run the metric functions over every activity's stored streams.

    This is what makes the Settings promise true: setting a max heart rate has to
    retroactively produce HR zones and TRIMP load for a decade of history, and
    those need the per-sample data — which lives in Parquet, not Postgres.
    """
    from sp_worker.tasks.ingest import _write_aggregates

    uid = UUID(user_id)
    changed = 0

    with sync_session_scope(uid) as session:
        user = session.get(User, uid)
        if user is None:
            return 0
        profile = AthleteProfile(
            ftp_w=user.ftp_w,
            max_hr_bpm=user.max_hr_bpm,
            resting_hr_bpm=user.resting_hr_bpm,
            weight_kg=user.weight_kg,
            sex=user.sex,
        )
        activities = (
            session.execute(select(Activity).where(Activity.user_id == uid)).scalars().all()
        )

        for activity in activities:
            streams = None
            if activity.has_streams and activity.stream_object_key:
                try:
                    streams = parquet_to_streams(get_bytes(activity.stream_object_key))
                except Exception as exc:
                    log.warning(
                        "metrics.recompute.stream_unavailable",
                        activity_id=str(activity.id),
                        error=str(exc)[:200],
                    )

            canonical = CanonicalActivity(
                source=activity.source,  # type: ignore[arg-type]
                start_time_utc=activity.start_time_utc,
                start_time_local=activity.start_time_local,
                utc_offset_s=activity.utc_offset_s,
                elapsed_time_s=activity.elapsed_time_s,
                moving_time_s=activity.moving_time_s,
                sport_type=activity.sport_type,
                avg_hr_bpm=activity.avg_hr_bpm,
                avg_power_w=activity.avg_power_w,
                avg_speed_mps=activity.avg_speed_mps,
                weighted_avg_power_w=activity.weighted_avg_power_w,
                perceived_exertion=activity.perceived_exertion,
            )

            metrics = analyze_activity(canonical, streams, profile)

            activity.trimp = metrics.trimp
            activity.tss = metrics.tss
            activity.training_load = metrics.training_load
            activity.load_source = metrics.load_source
            activity.intensity_factor = metrics.intensity_factor
            activity.efficiency_factor = metrics.efficiency_factor
            activity.decoupling_pct = metrics.decoupling_pct
            activity.normalized_power_w = metrics.normalized_power_w

            _write_aggregates(session, uid, activity.id, metrics)
            changed += 1

    log.info("metrics.recompute.done", user_id=user_id, activities=changed)
    return changed


def rebuild_daily_load(user_id: str) -> None:
    """Rebuild `daily_load` and the CTL/ATL/TSB series.

    The dense-calendar step is the important one: exponential decay over a sparse
    series silently inflates fitness, because a gap reads as "no decay happened"
    rather than "nothing was trained".
    """
    uid = UUID(user_id)
    with sync_session_scope(uid) as session:
        rows = session.execute(
            select(
                func.date(Activity.start_time_local).label("day"),
                func.coalesce(func.sum(Activity.training_load), 0.0),
                func.coalesce(func.sum(Activity.moving_time_s), 0),
                func.coalesce(func.sum(Activity.distance_m), 0.0),
                func.count(Activity.id),
            )
            .where(Activity.user_id == uid)
            .group_by("day")
            .order_by("day")
        ).all()

        if not rows:
            session.execute(text_delete_daily(uid))
            return

        loads = {row[0]: float(row[1] or 0) for row in rows}
        details = {row[0]: (int(row[2] or 0), float(row[3] or 0), int(row[4] or 0)) for row in rows}

        dense = build_daily_series(loads, start=min(loads), end=max(date.today(), max(loads)))
        series = compute_fitness_series(dense)

        session.execute(text_delete_daily(uid))
        session.execute(
            _daily_load_insert(),
            [
                {
                    "user_id": uid,
                    "day": point.day,
                    "load": round(point.load, 4),
                    "duration_s": details.get(point.day, (0, 0.0, 0))[0],
                    "distance_m": details.get(point.day, (0, 0.0, 0))[1],
                    "activity_count": details.get(point.day, (0, 0.0, 0))[2],
                    "ctl": round(point.ctl, 4),
                    "atl": round(point.atl, 4),
                    "tsb": round(point.tsb, 4),
                }
                for point in series
            ],
        )
    log.info("metrics.daily_load.rebuilt", user_id=user_id, days=len(series))


def text_delete_daily(user_id: UUID):
    from sp_db.models import DailyLoad

    return delete(DailyLoad).where(DailyLoad.user_id == user_id)


def _daily_load_insert():
    from sp_db.models import DailyLoad

    return DailyLoad.__table__.insert()


def rebuild_all_time_prs(user_id: str) -> None:
    """Flag the single best effort at each distance as the all-time PR."""
    uid = UUID(user_id)
    with sync_session_scope(uid) as session:
        session.execute(
            update(ActivityDistancePR)
            .where(ActivityDistancePR.user_id == uid)
            .values(is_all_time_best=False)
        )

        best_rows = session.execute(
            select(
                ActivityDistancePR.distance_m,
                func.min(ActivityDistancePR.time_s),
            )
            .where(ActivityDistancePR.user_id == uid)
            .group_by(ActivityDistancePR.distance_m)
        ).all()

        for distance_m, best_time in best_rows:
            winner = session.execute(
                select(ActivityDistancePR.activity_id)
                .where(
                    ActivityDistancePR.user_id == uid,
                    ActivityDistancePR.distance_m == distance_m,
                    ActivityDistancePR.time_s == best_time,
                )
                .limit(1)
            ).scalar_one_or_none()
            if winner is not None:
                session.execute(
                    update(ActivityDistancePR)
                    .where(
                        ActivityDistancePR.activity_id == winner,
                        ActivityDistancePR.distance_m == distance_m,
                    )
                    .values(is_all_time_best=True)
                )


def _gear_kind(sport_group: str) -> str:
    """Infer gear type from the sport it was used for.

    The export gives us only a free-text gear name, so the kind is inferred rather
    than known. It only drives an icon and the default replacement threshold.
    """
    if sport_group in ("run", "walk"):
        return "shoe"
    return "bike" if sport_group == "ride" else "other"


def rebuild_gear(user_id: str) -> None:
    """Roll gear mileage up from activities.

    Gear arrives from the export as a free-text name on each activity, so the gear
    row is created lazily the first time a name is seen.
    """
    uid = UUID(user_id)
    with sync_session_scope(uid) as session:
        rows = session.execute(
            select(
                Activity.extra["activitygear"].astext.label("gear_name"),
                func.coalesce(func.sum(Activity.distance_m), 0.0),
                func.count(Activity.id),
                Activity.sport_group,
            )
            .where(Activity.user_id == uid, Activity.extra["activitygear"].astext.isnot(None))
            .group_by("gear_name", Activity.sport_group)
        ).all()

        totals: dict[str, tuple[float, int, str]] = {}
        for name, distance, count, sport in rows:
            if not name or not name.strip():
                continue
            previous = totals.get(name, (0.0, 0, sport))
            totals[name] = (
                previous[0] + float(distance or 0),
                previous[1] + int(count),
                previous[2],
            )

        session.execute(delete(Gear).where(Gear.user_id == uid))
        for name, (distance, count, sport) in totals.items():
            session.add(
                Gear(
                    user_id=uid,
                    external_id=None,
                    kind=_gear_kind(sport),
                    name=name.strip()[:200],
                    distance_m=distance,
                    activity_count=count,
                )
            )


def rebuild_capabilities(user_id: str) -> None:
    """Recompute what data this user actually has.

    This is what stops a power-meter chart appearing for a phone-only runner
    (CLAUDE.md §5).
    """
    uid = UUID(user_id)
    with sync_session_scope(uid) as session:
        activities = session.execute(
            select(
                Activity.sport_group,
                Activity.available_channels,
                Activity.avg_hr_bpm,
                Activity.avg_power_w,
                Activity.avg_cadence_rpm,
                Activity.elevation_gain_m,
                Activity.perceived_exertion,
                Activity.start_time_local,
                Activity.extra["activitygear"].astext,
            ).where(Activity.user_id == uid)
        ).all()

        counts: dict[str, int] = defaultdict(int)
        first: dict[str, date] = {}
        last: dict[str, date] = {}

        def note(capability: str, day: date) -> None:
            counts[capability] += 1
            first[capability] = min(first.get(capability, day), day)
            last[capability] = max(last.get(capability, day), day)

        for row in activities:
            day = row[7].date()
            note(f"sport.{row[0]}", day)
            # Go through the shared mapping, never the raw column name — the
            # frontend checks `stream.heartrate`, not `stream.heartrate_bpm`.
            for column in row[1] or []:
                capability = capability_for_channel(column)
                if capability:
                    note(capability, day)
            for value, capability in (
                (row[2], "field.heartrate"),
                (row[3], "field.power"),
                (row[4], "field.cadence"),
                (row[5], "field.elevation"),
                (row[6], "field.rpe"),
                (row[8], "field.gear"),
            ):
                if value:
                    note(capability, day)

        session.execute(delete(UserCapability).where(UserCapability.user_id == uid))
        for capability, count in counts.items():
            session.add(
                UserCapability(
                    user_id=uid,
                    capability=capability,
                    activity_count=count,
                    first_seen=first.get(capability),
                    last_seen=last.get(capability),
                )
            )

    log.info("metrics.capabilities.rebuilt", user_id=user_id, count=len(counts))
