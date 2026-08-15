"""SQLAlchemy 2.0 typed models.

Mirrors DATA_MODEL.md and AUTH.md. Two structural rules from CLAUDE.md hold
throughout:

* Every user-scoped table carries ``user_id`` and gets an RLS policy (§4.5).
* Every measure is nullable — ``None`` means "not measured", never ``0`` (§4).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --------------------------------------------------------------------------- #
# Identity (AUTH.md)
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: NULL when the user only ever signed in with Google.
    password_hash: Mapped[str | None] = mapped_column(Text)

    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    profile_photo_url: Mapped[str | None] = mapped_column(Text)

    # fitness profile — inputs to the metric functions
    weight_kg: Mapped[float | None] = mapped_column(Float)
    ftp_w: Mapped[int | None] = mapped_column(Integer)
    max_hr_bpm: Mapped[int | None] = mapped_column(Integer)
    resting_hr_bpm: Mapped[int | None] = mapped_column(Integer)
    sex: Mapped[str | None] = mapped_column(String(1))
    measurement_pref: Mapped[str] = mapped_column(Text, nullable=False, server_default="metric")
    timezone: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list[AuthIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    strava: Mapped[StravaConnection | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AuthIdentity(Base):
    """A linked external identity. One row per provider per user."""

    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_sub", name="auth_identities_provider_uq"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    #: Google's stable `sub` claim. Never the email — emails get reassigned.
    provider_sub: Mapped[str] = mapped_column(Text, nullable=False)
    email_at_link: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()

    user: Mapped[User] = relationship(back_populates="identities")


class Session(Base):
    """Opaque server-side session. The row id is never the cookie value — we store
    only the token hash, so a database read cannot be replayed (AUTH.md §4)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_created_from: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = _now()


class AuthEvent(Base):
    """Every signup/login/reset attempt, success or failure. Drives throttling."""

    __tablename__ = "auth_events"
    __table_args__ = (
        Index("auth_events_email_time", "email_tried", "created_at"),
        Index("auth_events_ip_time", "ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    email_tried: Mapped[str | None] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class StravaConnection(Base):
    """Strava is a data connection, not an identity (AUTH.md §1)."""

    __tablename__ = "strava_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    strava_athlete_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    access_token_enc: Mapped[bytes] = mapped_column(nullable=False)
    refresh_token_enc: Mapped[bytes] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    user: Mapped[User] = relationship(back_populates="strava")


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

UPLOAD_STATUSES = (
    "awaiting_file",
    "queued",
    "inspecting",
    "fast_path",
    "deep_parse",
    "finalizing",
    "complete",
    "failed",
)


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in UPLOAD_STATUSES) + ")",
            name="uploads_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="awaiting_file")

    items_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    items_done: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    activities_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The moment the dashboard becomes usable — the fast path's whole purpose.
    fast_path_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class IngestItem(Base):
    """One row per file inside the zip. This is what makes an import resumable and
    lets one corrupt .fit fail without killing the other 2,999 (CLAUDE.md §4.6)."""

    __tablename__ = "ingest_items"
    __table_args__ = (
        UniqueConstraint("upload_id", "member_path", name="ingest_items_member_uq"),
        Index("ingest_items_upload_status", "upload_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_path: Mapped[str] = mapped_column(Text, nullable=False)
    member_size: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    activity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index(
            "activities_strava_uq",
            "user_id",
            "strava_activity_id",
            unique=True,
            postgresql_where="strava_activity_id IS NOT NULL",
        ),
        Index(
            "activities_hash_uq",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where="strava_activity_id IS NULL AND content_hash IS NOT NULL",
        ),
        Index("activities_user_time", "user_id", "start_time_utc"),
        Index("activities_user_sport_time", "user_id", "sport_group", "start_time_utc"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    strava_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    #: 0 = summary only (CSV fast path), 1 = streams parsed. The deep parse upgrades
    #: rows in place, so the dashboard works before it finishes (INGESTION.md §3).
    detail_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Naive wall-clock. A UTC timestamp cannot answer "do I run in the morning?".
    start_time_local: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    utc_offset_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    elapsed_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    moving_time_s: Mapped[int | None] = mapped_column(Integer)

    sport_type: Mapped[str] = mapped_column(Text, nullable=False)
    sport_group: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_indoor: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_commute: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_race: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    gear_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("gear.id", ondelete="SET NULL")
    )

    # measures — SI units, all nullable
    distance_m: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    elevation_loss_m: Mapped[float | None] = mapped_column(Float)
    avg_speed_mps: Mapped[float | None] = mapped_column(Float)
    max_speed_mps: Mapped[float | None] = mapped_column(Float)
    avg_hr_bpm: Mapped[float | None] = mapped_column(Float)
    max_hr_bpm: Mapped[float | None] = mapped_column(Float)
    avg_cadence_rpm: Mapped[float | None] = mapped_column(Float)
    avg_power_w: Mapped[float | None] = mapped_column(Float)
    max_power_w: Mapped[float | None] = mapped_column(Float)
    weighted_avg_power_w: Mapped[float | None] = mapped_column(Float)
    kilojoules: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float | None] = mapped_column(Float)
    avg_temp_c: Mapped[float | None] = mapped_column(Float)
    perceived_exertion: Mapped[int | None] = mapped_column(SmallInteger)
    relative_effort: Mapped[float | None] = mapped_column(Float)

    # derived
    trimp: Mapped[float | None] = mapped_column(Float)
    tss: Mapped[float | None] = mapped_column(Float)
    training_load: Mapped[float | None] = mapped_column(Float)
    #: Which rung of the fallback ladder produced training_load. Must travel with
    #: the number so the UI can label estimates (FEATURES.md).
    load_source: Mapped[str | None] = mapped_column(Text)
    intensity_factor: Mapped[float | None] = mapped_column(Float)
    efficiency_factor: Mapped[float | None] = mapped_column(Float)
    decoupling_pct: Mapped[float | None] = mapped_column(Float)
    normalized_power_w: Mapped[float | None] = mapped_column(Float)

    start_lat: Mapped[float | None] = mapped_column(Float)
    start_lng: Mapped[float | None] = mapped_column(Float)
    polyline: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    stream_object_key: Mapped[str | None] = mapped_column(Text)
    has_streams: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    available_channels: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ActivityZoneTime(Base):
    __tablename__ = "activity_zone_time"

    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    zone_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    zone_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class ActivityBestEffort(Base):
    """The mean-maximal curve. ~40 rows per activity, not 4,000."""

    __tablename__ = "activity_best_efforts"
    __table_args__ = (
        Index("best_efforts_user_metric", "user_id", "metric", "duration_s", "value"),
    )

    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    duration_s: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class ActivityDistancePR(Base):
    __tablename__ = "activity_distance_prs"
    __table_args__ = (Index("distance_prs_user", "user_id", "distance_m", "time_s"),)

    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    distance_m: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    time_s: Mapped[float] = mapped_column(Float, nullable=False)
    is_all_time_best: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class DailyLoad(Base):
    """One row per user per calendar day, **including rest days with load 0** —
    without them the exponential decay in the fitness model is wrong."""

    __tablename__ = "daily_load"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    load: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    distance_m: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    activity_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    ctl: Mapped[float | None] = mapped_column(Float)
    atl: Mapped[float | None] = mapped_column(Float)
    tsb: Mapped[float | None] = mapped_column(Float)


class Gear(Base):
    __tablename__ = "gear"
    __table_args__ = (UniqueConstraint("user_id", "external_id", name="gear_external_uq"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    retired: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    distance_m: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    activity_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    alert_at_m: Mapped[float | None] = mapped_column(Float)


class UserCapability(Base):
    """What data this user actually has. Drives which charts render (CLAUDE.md §5)."""

    __tablename__ = "user_capabilities"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    activity_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)


#: Tables that carry user data and therefore get an RLS policy (CLAUDE.md §4.5).
RLS_TABLES: tuple[str, ...] = (
    "activities",
    "activity_zone_time",
    "activity_best_efforts",
    "activity_distance_prs",
    "daily_load",
    "gear",
    "user_capabilities",
    "uploads",
    "ingest_items",
)
