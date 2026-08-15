"""Profile, capabilities, and account deletion."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from sp_core.storage.objects import delete_prefix
from sp_db.models import Activity, StravaConnection, User, UserCapability
from sqlalchemy import delete, func, select

from sp_api.deps import CurrentUser, DbSession
from sp_api.enqueue import enqueue_recompute
from sp_api.schemas import (
    CapabilitiesResponse,
    CapabilityOut,
    Message,
    ProfileUpdate,
    UserOut,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/me", tags=["me"])

#: Changing any of these invalidates every derived training-load number.
_RECOMPUTE_TRIGGERS = ("ftp_w", "max_hr_bpm", "resting_hr_bpm", "sex")


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(session: DbSession, user: CurrentUser) -> CapabilitiesResponse:
    """What data this user actually has.

    The frontend renders the chart registry filtered by this. A chart with no
    backing data is never drawn as an empty chart (CLAUDE.md §5).
    """
    rows = (
        (
            await session.execute(
                select(UserCapability)
                .where(UserCapability.user_id == user.id)
                .order_by(UserCapability.activity_count.desc())
            )
        )
        .scalars()
        .all()
    )

    totals = (
        await session.execute(
            select(
                func.count(Activity.id),
                func.min(Activity.start_time_local),
                func.max(Activity.start_time_local),
            ).where(Activity.user_id == user.id)
        )
    ).one()

    sports = (
        (
            await session.execute(
                select(Activity.sport_group)
                .where(Activity.user_id == user.id)
                .group_by(Activity.sport_group)
                .order_by(func.count().desc())
            )
        )
        .scalars()
        .all()
    )

    return CapabilitiesResponse(
        capabilities=[
            CapabilityOut(
                capability=row.capability,
                activity_count=row.activity_count,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
            for row in rows
        ],
        total_activities=int(totals[0] or 0),
        first_activity=totals[1].date() if totals[1] else None,
        last_activity=totals[2].date() if totals[2] else None,
        sports=list(sports),
    )


@router.patch("/profile", response_model=UserOut)
async def update_profile(payload: ProfileUpdate, session: DbSession, user: CurrentUser) -> UserOut:
    """Update the fitness profile.

    Changing FTP or HR thresholds re-derives training load for every activity, so
    we enqueue a recompute rather than silently leaving stale numbers on screen.
    """
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    needs_recompute = any(field in changes for field in _RECOMPUTE_TRIGGERS)

    for field, value in changes.items():
        setattr(user, field, value)

    # Commit before enqueueing: the worker reads this user's thresholds within
    # milliseconds, and reading them pre-commit means it recomputes the entire
    # history against the *old* values and silently produces nothing.
    await session.commit()

    if needs_recompute:
        enqueue_recompute(str(user.id))
        log.info("profile.recompute_enqueued", user_id=str(user.id), fields=list(changes))

    from sp_api.routers.auth import _user_out

    return await _user_out(session, user)


@router.delete("/account", response_model=Message)
async def delete_account(session: DbSession, user: CurrentUser) -> Message:
    """Real deletion, not a soft-delete flag (CLAUDE.md §8).

    Cascades handle the relational side; object storage is cleared by prefix.
    """
    user_id = user.id
    delete_prefix(f"raw/{user_id}/")
    delete_prefix(f"streams/{user_id}/")

    await session.execute(delete(User).where(User.id == user_id))
    log.info("account.deleted", user_id=str(user_id))
    return Message(message="Your account and all of your data have been deleted.")


@router.delete("/strava", response_model=Message)
async def disconnect_strava(session: DbSession, user: CurrentUser) -> Message:
    """Disconnect Strava without touching the account.

    This is the payoff of decoupling identity from Strava (AUTH.md): losing the
    data connection never costs the user their login or their imported history.
    """
    result = await session.execute(
        delete(StravaConnection).where(StravaConnection.user_id == user.id)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Strava isn't connected.")
    return Message(
        message=(
            "Strava disconnected. Your imported history is still here, "
            "and your account is unchanged."
        )
    )
