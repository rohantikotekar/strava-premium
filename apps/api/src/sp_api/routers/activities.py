"""Activity list, detail, and stream access."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import numpy as np
from fastapi import APIRouter, HTTPException, Query, status
from sp_core.canonical.activity import Channel
from sp_core.storage.objects import get_bytes
from sp_core.storage.parquet import downsample, parquet_to_streams
from sp_db.models import (
    Activity,
    ActivityBestEffort,
    ActivityDistancePR,
    ActivityZoneTime,
)
from sqlalchemy import func, select

from sp_api.deps import CurrentUser, DbSession
from sp_api.schemas import (
    ActivityDetail,
    ActivityListItem,
    ActivityPage,
    StreamsResponse,
)

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=ActivityPage)
async def list_activities(
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sport: str | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> ActivityPage:
    conditions = [Activity.user_id == user.id]
    if sport and sport != "all":
        conditions.append(Activity.sport_group == sport)
    if search:
        conditions.append(Activity.name.ilike(f"%{search}%"))
    if date_from:
        conditions.append(Activity.start_time_local >= date_from)
    if date_to:
        conditions.append(Activity.start_time_local <= date_to)

    total = (await session.execute(select(func.count(Activity.id)).where(*conditions))).scalar_one()

    rows = (
        (
            await session.execute(
                select(Activity)
                .where(*conditions)
                .order_by(Activity.start_time_local.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return ActivityPage(
        items=[ActivityListItem.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{activity_id}", response_model=ActivityDetail)
async def get_activity(activity_id: UUID, session: DbSession, user: CurrentUser) -> ActivityDetail:
    activity = (
        await session.execute(
            select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
        )
    ).scalar_one_or_none()
    if activity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such activity.")

    detail = ActivityDetail.model_validate(activity)

    zone_rows = (
        await session.execute(
            select(ActivityZoneTime).where(ActivityZoneTime.activity_id == activity_id)
        )
    ).scalars()
    for row in zone_rows:
        detail.zone_time.setdefault(row.zone_kind, {})[row.zone_index] = row.seconds

    effort_rows = (
        await session.execute(
            select(ActivityBestEffort).where(ActivityBestEffort.activity_id == activity_id)
        )
    ).scalars()
    for row in effort_rows:
        detail.best_efforts.setdefault(row.metric, {})[row.duration_s] = row.value

    pr_rows = (
        await session.execute(
            select(ActivityDistancePR).where(ActivityDistancePR.activity_id == activity_id)
        )
    ).scalars()
    detail.distance_prs = {row.distance_m: row.time_s for row in pr_rows}

    return detail


@router.get("/{activity_id}/streams", response_model=StreamsResponse)
async def get_streams(
    activity_id: UUID,
    session: DbSession,
    user: CurrentUser,
    max_points: int = Query(2000, ge=100, le=20000),
) -> StreamsResponse:
    """Per-sample channels, downsampled for charting.

    Read from one Parquet object rather than a table — per-point data never goes
    in Postgres (ARCHITECTURE.md §3). Downsampling is min/max preserving so a
    30-second power spike survives being drawn 800px wide.
    """
    activity = (
        await session.execute(
            select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
        )
    ).scalar_one_or_none()
    if activity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such activity.")
    if not activity.has_streams or not activity.stream_object_key:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="This activity has no detailed data — it came from the summary index only.",
        )

    try:
        raw = get_bytes(activity.stream_object_key)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stream data is temporarily unavailable."
        ) from exc

    streams = downsample(parquet_to_streams(raw), max_points)

    channels: dict[str, list[float | None]] = {}
    for channel, values in streams.channels.items():
        if channel is not Channel.TIME and not streams.has(channel):
            continue
        channels[channel.value] = [
            None if not np.isfinite(v) else round(float(v), 4) for v in values
        ]

    return StreamsResponse(activity_id=activity_id, n_samples=streams.n_samples, channels=channels)
