"""Chart endpoints.

One endpoint per chart, each returning series plus the metadata the UI needs to
render **honestly**: whether the numbers are estimates, and what fraction of
activities actually carried the required data. A chart built from 312 of 1,208
activities must say so rather than quietly averaging a biased subset
(FRONTEND_DESIGN.md § edge cases).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sp_core.metrics.curves import DEFAULT_PR_DISTANCES_M
from sp_core.metrics.zones import HR_ZONE_LABELS, POWER_ZONE_LABELS
from sp_db.models import (
    Activity,
    ActivityBestEffort,
    ActivityDistancePR,
    ActivityZoneTime,
    DailyLoad,
    Gear,
)
from sqlalchemy import func, select

from sp_api.deps import CurrentUser, DbSession
from sp_api.schemas import ChartMeta, ChartResponse, ChartSeries, DashboardSummary, StatTile

router = APIRouter(prefix="/charts", tags=["charts"])

_RANGE_DAYS: dict[str, int | None] = {
    "4w": 28,
    "3m": 91,
    "6m": 183,
    "1y": 365,
    "2y": 730,
    "all": None,
}


def _range_start(range_key: str) -> date | None:
    days = _RANGE_DAYS.get(range_key, 365)
    return None if days is None else date.today() - timedelta(days=days)


def _scope(user_id: Any, range_key: str, sport: str | None) -> list[Any]:
    conditions: list[Any] = [Activity.user_id == user_id]
    start = _range_start(range_key)
    if start is not None:
        conditions.append(Activity.start_time_local >= datetime.combine(start, datetime.min.time()))
    if sport and sport != "all":
        conditions.append(Activity.sport_group == sport)
    return conditions


async def _coverage(session: DbSession, conditions: list[Any], column: Any) -> tuple[int, int]:
    """(activities with this field, activities in scope)."""
    total = (await session.execute(select(func.count(Activity.id)).where(*conditions))).scalar_one()
    used = (
        await session.execute(
            select(func.count(Activity.id)).where(*conditions, column.isnot(None))
        )
    ).scalar_one()
    return int(used), int(total)


def _coverage_note(used: int, total: int, what: str) -> str | None:
    if total == 0 or used == total:
        return None
    return f"Based on {used:,} of {total:,} activities that have {what}."


# --------------------------------------------------------------------------- #


@router.get("/dashboard-summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: DbSession,
    user: CurrentUser,
    range: str = Query("4w"),
    sport: str | None = None,
) -> DashboardSummary:
    """Hero figure + KPI row, each with a delta against the preceding period."""
    conditions = _scope(user.id, range, sport)

    aggregate = select(
        func.coalesce(func.sum(Activity.distance_m), 0.0),
        func.coalesce(func.sum(Activity.moving_time_s), 0),
        func.coalesce(func.sum(Activity.elevation_gain_m), 0.0),
        func.count(Activity.id),
        func.coalesce(func.sum(Activity.training_load), 0.0),
    )
    current = (await session.execute(aggregate.where(*conditions))).one()

    # Same-length preceding window, for the delta.
    days = _RANGE_DAYS.get(range) or 365
    previous_start = datetime.combine(date.today() - timedelta(days=days * 2), datetime.min.time())
    previous_end = datetime.combine(date.today() - timedelta(days=days), datetime.min.time())
    previous_conditions = [
        Activity.user_id == user.id,
        Activity.start_time_local >= previous_start,
        Activity.start_time_local < previous_end,
    ]
    if sport and sport != "all":
        previous_conditions.append(Activity.sport_group == sport)
    previous = (await session.execute(aggregate.where(*previous_conditions))).one()

    def delta(now: float, before: float) -> float | None:
        if not before:
            return None
        return (now - before) / before * 100.0

    # Daily distance for the sparklines.
    daily = (
        await session.execute(
            select(
                func.date(Activity.start_time_local).label("day"),
                func.coalesce(func.sum(Activity.distance_m), 0.0),
            )
            .where(*conditions)
            .group_by("day")
            .order_by("day")
        )
    ).all()
    spark = [float(row[1] or 0) for row in daily]

    active_days = {row[0] for row in daily}
    streak, longest = _streaks(sorted(active_days))

    return DashboardSummary(
        period_label={
            "4w": "Last 4 weeks",
            "3m": "Last 3 months",
            "6m": "Last 6 months",
            "1y": "Last year",
            "2y": "Last 2 years",
            "all": "All time",
        }.get(range, range),
        hero=StatTile(
            key="distance",
            label="Distance",
            value=float(current[0]),
            unit="m",
            delta_pct=delta(float(current[0]), float(previous[0])),
            sparkline=spark,
        ),
        tiles=[
            StatTile(
                key="time",
                label="Moving time",
                value=float(current[1]),
                unit="s",
                delta_pct=delta(float(current[1]), float(previous[1])),
            ),
            StatTile(
                key="elevation",
                label="Elevation",
                value=float(current[2]),
                unit="m",
                delta_pct=delta(float(current[2]), float(previous[2])),
            ),
            StatTile(
                key="activities",
                label="Activities",
                value=float(current[3]),
                unit="count",
                delta_pct=delta(float(current[3]), float(previous[3])),
            ),
            StatTile(
                key="load",
                label="Training load",
                value=float(current[4]),
                unit="load",
                delta_pct=delta(float(current[4]), float(previous[4])),
            ),
        ],
        streak_days=streak,
        longest_streak_days=longest,
        active_days=len(active_days),
    )


def _streaks(days: list[date]) -> tuple[int, int]:
    """(current streak ending today/yesterday, longest streak)."""
    if not days:
        return 0, 0
    longest = run = 1
    for previous, current in pairwise(days):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)

    today = date.today()
    current_streak = 0
    if days[-1] in (today, today - timedelta(days=1)):
        current_streak = 1
        for previous, following in zip(reversed(days[:-1]), reversed(days[1:]), strict=False):
            if (following - previous).days == 1:
                current_streak += 1
            else:
                break
    return current_streak, longest


@router.get("/{chart_id}", response_model=ChartResponse)
async def get_chart(
    chart_id: str,
    session: DbSession,
    user: CurrentUser,
    range: str = Query("1y"),
    sport: str | None = None,
) -> ChartResponse:
    handler = _HANDLERS.get(chart_id)
    if handler is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown chart '{chart_id}'.")
    return await handler(session, user, range, sport)


# --------------------------------------------------------------------------- #
# Individual charts
# --------------------------------------------------------------------------- #


async def _weekly_volume(
    session: DbSession, user: Any, range: str, sport: str | None
) -> ChartResponse:
    conditions = _scope(user.id, range, sport)
    week = func.date_trunc("week", Activity.start_time_local).label("week")

    rows = (
        await session.execute(
            select(
                week,
                func.coalesce(func.sum(Activity.distance_m), 0.0),
                func.coalesce(func.sum(Activity.moving_time_s), 0),
                func.count(Activity.id),
            )
            .where(*conditions)
            .group_by(week)
            .order_by(week)
        )
    ).all()

    points = [
        {
            "week": row[0].date().isoformat(),
            "distance_m": float(row[1] or 0),
            "moving_time_s": int(row[2] or 0),
            "activities": int(row[3]),
        }
        for row in rows
    ]
    # 4-week rolling mean, same unit as the bars — so it shares one axis.
    for index, point in enumerate(points):
        window = points[max(0, index - 3) : index + 1]
        point["rolling_4w_m"] = sum(p["distance_m"] for p in window) / len(window)

    return ChartResponse(
        meta=ChartMeta(
            chart_id="weekly-volume",
            title="Weekly volume",
            question="How much am I training?",
            unit="m",
            activities_used=sum(p["activities"] for p in points),
            activities_total=sum(p["activities"] for p in points),
        ),
        series=[ChartSeries(key="weekly", label="Weekly distance", points=points)],
    )


async def _fitness(session: DbSession, user: Any, range: str, sport: str | None) -> ChartResponse:
    start = _range_start(range)
    conditions = [DailyLoad.user_id == user.id]
    if start is not None:
        conditions.append(DailyLoad.day >= start)

    rows = (
        (await session.execute(select(DailyLoad).where(*conditions).order_by(DailyLoad.day)))
        .scalars()
        .all()
    )

    points = [
        {
            "day": row.day.isoformat(),
            "load": round(float(row.load or 0), 2),
            "ctl": round(float(row.ctl or 0), 2),
            "atl": round(float(row.atl or 0), 2),
            "tsb": round(float(row.tsb or 0), 2),
        }
        for row in rows
    ]

    # If any activity in scope fell back off the TSS/TRIMP rungs, the whole series
    # is partly estimated and must say so.
    estimated = (
        await session.execute(
            select(func.count(Activity.id)).where(
                Activity.user_id == user.id, Activity.load_source.in_(("rpe", "duration"))
            )
        )
    ).scalar_one()
    total = (
        await session.execute(select(func.count(Activity.id)).where(Activity.user_id == user.id))
    ).scalar_one()

    return ChartResponse(
        meta=ChartMeta(
            chart_id="fitness",
            title="Fitness & freshness",
            question="Am I building fitness, and am I fresh?",
            unit="load",
            is_estimate=bool(estimated),
            estimate_reason=(
                f"{estimated:,} of {total:,} activities have no power or heart-rate data, so their "
                "training load is estimated from duration. Set your max HR or FTP for accuracy."
                if estimated
                else None
            ),
            activities_used=int(total - estimated),
            activities_total=int(total),
        ),
        series=[ChartSeries(key="fitness", label="Fitness, fatigue and form", points=points)],
    )


async def _calendar(session: DbSession, user: Any, range: str, sport: str | None) -> ChartResponse:
    start = _range_start(range if range != "all" else "1y") or date.today() - timedelta(days=365)
    rows = (
        (
            await session.execute(
                select(DailyLoad)
                .where(DailyLoad.user_id == user.id, DailyLoad.day >= start)
                .order_by(DailyLoad.day)
            )
        )
        .scalars()
        .all()
    )

    points = [
        {
            "day": row.day.isoformat(),
            "load": round(float(row.load or 0), 2),
            "duration_s": int(row.duration_s or 0),
            "distance_m": float(row.distance_m or 0),
            "activities": int(row.activity_count or 0),
        }
        for row in rows
    ]
    return ChartResponse(
        meta=ChartMeta(
            chart_id="calendar",
            title="Training calendar",
            question="How consistent have I been?",
            unit="load",
            activities_used=sum(p["activities"] for p in points),
            activities_total=sum(p["activities"] for p in points),
        ),
        series=[ChartSeries(key="calendar", label="Daily training load", points=points)],
    )


async def _sport_mix(session: DbSession, user: Any, range: str, sport: str | None) -> ChartResponse:
    conditions = _scope(user.id, range, None)
    month = func.date_trunc("month", Activity.start_time_local).label("month")

    rows = (
        await session.execute(
            select(month, Activity.sport_group, func.coalesce(func.sum(Activity.moving_time_s), 0))
            .where(*conditions)
            .group_by(month, Activity.sport_group)
            .order_by(month)
        )
    ).all()

    by_month: dict[str, dict[str, Any]] = defaultdict(dict)
    for month_value, sport_group, seconds in rows:
        key = month_value.date().isoformat()
        by_month[key]["month"] = key
        by_month[key][sport_group] = int(seconds or 0)

    return ChartResponse(
        meta=ChartMeta(
            chart_id="sport-mix",
            title="Sport mix",
            question="What am I actually spending time on?",
            unit="s",
        ),
        series=[
            ChartSeries(
                key="sport-mix",
                label="Monthly time by sport",
                points=[by_month[key] for key in sorted(by_month)],
            )
        ],
    )


async def _year_over_year(
    session: DbSession, user: Any, range: str, sport: str | None
) -> ChartResponse:
    conditions = [Activity.user_id == user.id]
    if sport and sport != "all":
        conditions.append(Activity.sport_group == sport)

    rows = (
        await session.execute(
            select(
                func.extract("year", Activity.start_time_local).label("year"),
                func.extract("doy", Activity.start_time_local).label("doy"),
                func.coalesce(func.sum(Activity.distance_m), 0.0),
            )
            .where(*conditions)
            .group_by("year", "doy")
            .order_by("year", "doy")
        )
    ).all()

    cumulative: dict[int, float] = defaultdict(float)
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for year_value, doy, distance in rows:
        year = int(year_value)
        cumulative[year] += float(distance or 0)
        by_year[year].append({"doy": int(doy), "distance_m": cumulative[year]})

    return ChartResponse(
        meta=ChartMeta(
            chart_id="year-over-year",
            title="Year over year",
            question="Am I ahead of where I was?",
            unit="m",
        ),
        series=[
            ChartSeries(key=str(year), label=str(year), points=points)
            for year, points in sorted(by_year.items())
        ],
    )


async def _zone_chart(
    session: DbSession, user: Any, range: str, sport: str | None, kind: str
) -> ChartResponse:
    conditions = _scope(user.id, range, sport)
    labels = HR_ZONE_LABELS if kind == "hr" else POWER_ZONE_LABELS

    rows = (
        await session.execute(
            select(ActivityZoneTime.zone_index, func.sum(ActivityZoneTime.seconds))
            .join(Activity, Activity.id == ActivityZoneTime.activity_id)
            .where(*conditions, ActivityZoneTime.zone_kind == kind)
            .group_by(ActivityZoneTime.zone_index)
            .order_by(ActivityZoneTime.zone_index)
        )
    ).all()

    used, total = await _coverage(
        session, conditions, Activity.avg_hr_bpm if kind == "hr" else Activity.avg_power_w
    )

    points = [
        {
            "zone": int(index),
            "label": labels[int(index) - 1] if int(index) - 1 < len(labels) else f"Z{index}",
            "seconds": int(seconds or 0),
        }
        for index, seconds in rows
    ]

    return ChartResponse(
        meta=ChartMeta(
            chart_id=f"{kind}-zones",
            title="Heart-rate zones" if kind == "hr" else "Power zones",
            question="Where is my training time actually going?",
            unit="s",
            coverage_note=_coverage_note(used, total, "heart rate" if kind == "hr" else "power"),
            activities_used=used,
            activities_total=total,
        ),
        series=[ChartSeries(key=kind, label="Time in zone", points=points)],
    )


async def _curve(
    session: DbSession, user: Any, range: str, sport: str | None, metric: str
) -> ChartResponse:
    all_time = (
        await session.execute(
            select(ActivityBestEffort.duration_s, func.max(ActivityBestEffort.value))
            .where(ActivityBestEffort.user_id == user.id, ActivityBestEffort.metric == metric)
            .group_by(ActivityBestEffort.duration_s)
            .order_by(ActivityBestEffort.duration_s)
        )
    ).all()

    conditions = _scope(user.id, range, sport)
    recent = (
        await session.execute(
            select(ActivityBestEffort.duration_s, func.max(ActivityBestEffort.value))
            .join(Activity, Activity.id == ActivityBestEffort.activity_id)
            .where(*conditions, ActivityBestEffort.metric == metric)
            .group_by(ActivityBestEffort.duration_s)
            .order_by(ActivityBestEffort.duration_s)
        )
    ).all()

    used, total = await _coverage(
        session, conditions, Activity.avg_power_w if metric == "power" else Activity.avg_speed_mps
    )

    return ChartResponse(
        meta=ChartMeta(
            chart_id=f"{metric}-curve",
            title="Power curve" if metric == "power" else "Pace curve",
            question="What can I sustain, and for how long?",
            unit="W" if metric == "power" else "m/s",
            coverage_note=_coverage_note(used, total, "power" if metric == "power" else "speed"),
            activities_used=used,
            activities_total=total,
        ),
        series=[
            ChartSeries(
                key="all_time",
                label="All time",
                points=[{"duration_s": int(d), "value": float(v)} for d, v in all_time],
            ),
            ChartSeries(
                key="selected",
                label="Selected period",
                points=[{"duration_s": int(d), "value": float(v)} for d, v in recent],
            ),
        ],
    )


async def _records(session: DbSession, user: Any, range: str, sport: str | None) -> ChartResponse:
    rows = (
        await session.execute(
            select(
                ActivityDistancePR.distance_m,
                func.min(ActivityDistancePR.time_s),
                func.count(),
            )
            .where(ActivityDistancePR.user_id == user.id)
            .group_by(ActivityDistancePR.distance_m)
            .order_by(ActivityDistancePR.distance_m)
        )
    ).all()

    best = {int(distance): (float(time), int(count)) for distance, time, count in rows}
    points = [
        {
            "distance_m": distance,
            "time_s": best[distance][0],
            "attempts": best[distance][1],
        }
        for distance in DEFAULT_PR_DISTANCES_M
        if distance in best
    ]

    return ChartResponse(
        meta=ChartMeta(
            chart_id="records",
            title="Personal records",
            question="What are my best efforts?",
            unit="s",
            activities_used=len(points),
        ),
        series=[ChartSeries(key="records", label="Best times", points=points)],
    )


async def _gear(session: DbSession, user: Any, range: str, sport: str | None) -> ChartResponse:
    rows = (
        (
            await session.execute(
                select(Gear).where(Gear.user_id == user.id).order_by(Gear.distance_m.desc())
            )
        )
        .scalars()
        .all()
    )

    points = [
        {
            "id": str(row.id),
            "name": row.name,
            "kind": row.kind,
            "distance_m": float(row.distance_m or 0),
            "activities": int(row.activity_count or 0),
            "alert_at_m": float(row.alert_at_m) if row.alert_at_m else None,
            "retired": row.retired,
        }
        for row in rows
    ]
    return ChartResponse(
        meta=ChartMeta(
            chart_id="gear",
            title="Gear mileage",
            question="When do I need new shoes?",
            unit="m",
            activities_used=sum(p["activities"] for p in points),
        ),
        series=[ChartSeries(key="gear", label="Distance per item", points=points)],
    )


_HANDLERS = {
    "weekly-volume": _weekly_volume,
    "fitness": _fitness,
    "calendar": _calendar,
    "sport-mix": _sport_mix,
    "year-over-year": _year_over_year,
    "hr-zones": lambda s, u, r, sp: _zone_chart(s, u, r, sp, "hr"),
    "power-zones": lambda s, u, r, sp: _zone_chart(s, u, r, sp, "power"),
    "power-curve": lambda s, u, r, sp: _curve(s, u, r, sp, "power"),
    "pace-curve": lambda s, u, r, sp: _curve(s, u, r, sp, "speed"),
    "records": _records,
    "gear": _gear,
}
