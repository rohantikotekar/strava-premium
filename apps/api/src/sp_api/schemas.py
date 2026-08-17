"""Pydantic request/response models — the API's contract.

The frontend's TypeScript types are generated from the OpenAPI doc these produce
(`pnpm gen:api`), so if the UI needs a field it gets added here first
(CLAUDE.md §3).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Message(BaseModel):
    """Generic, deliberately non-committal response.

    Signup and password-reset both return this regardless of whether the account
    exists — the API must not reveal which addresses are registered (AUTH.md §5).
    """

    message: str
    ok: bool = True
    #: Set only when ``auth_bearer_tokens`` is on. See UserOut.session_token.
    session_token: str | None = None


# ---- auth -------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    first_name: str | None = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    """Never contains password_hash or any Strava token (CLAUDE.md §8)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    email_verified: bool
    first_name: str | None
    last_name: str | None
    profile_photo_url: str | None
    measurement_pref: str
    weight_kg: float | None
    ftp_w: int | None
    max_hr_bpm: int | None
    resting_hr_bpm: int | None
    sex: str | None
    has_password: bool
    has_google: bool
    strava_connected: bool
    created_at: datetime
    #: The raw session token, returned by signup/login only when
    #: ``auth_bearer_tokens`` is on. Never populated by /me or any other
    #: endpoint — this is the one moment the client is allowed to see it.
    session_token: str | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    last_seen_at: datetime
    ip_created_from: str | None
    user_agent: str | None
    is_current: bool = False


class AuthProviders(BaseModel):
    """Which sign-in buttons the login page should render."""

    google: bool
    strava: bool


class ProfileUpdate(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    weight_kg: float | None = Field(default=None, gt=20, lt=300)
    ftp_w: int | None = Field(default=None, gt=0, lt=800)
    max_hr_bpm: int | None = Field(default=None, gt=100, lt=260)
    resting_hr_bpm: int | None = Field(default=None, gt=20, lt=120)
    sex: Literal["M", "F", "X"] | None = None
    measurement_pref: Literal["metric", "imperial"] | None = None


# ---- uploads / imports ------------------------------------------------------


class UploadCreate(BaseModel):
    filename: str = Field(max_length=255)
    size_bytes: int = Field(gt=0)


class UploadCreated(BaseModel):
    upload_id: UUID
    upload_url: str
    method: str = "PUT"
    object_key: str


class ImportStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    filename: str | None
    items_total: int
    items_done: int
    items_failed: int
    activities_found: int
    error: str | None
    created_at: datetime
    fast_path_done_at: datetime | None
    completed_at: datetime | None

    @property
    def dashboard_ready(self) -> bool:
        return self.fast_path_done_at is not None


class FailedItem(BaseModel):
    member_path: str
    error: str | None


# ---- capabilities -----------------------------------------------------------


class CapabilityOut(BaseModel):
    capability: str
    activity_count: int
    first_seen: date | None
    last_seen: date | None


class CapabilitiesResponse(BaseModel):
    """Drives which charts the frontend renders at all (CLAUDE.md §5)."""

    capabilities: list[CapabilityOut]
    total_activities: int
    first_activity: date | None
    last_activity: date | None
    sports: list[str]


# ---- activities -------------------------------------------------------------


class ActivityListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    strava_activity_id: int | None
    name: str | None
    sport_type: str
    sport_group: str
    start_time_local: datetime
    elapsed_time_s: int
    moving_time_s: int | None
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr_bpm: float | None
    avg_power_w: float | None
    avg_speed_mps: float | None
    training_load: float | None
    load_source: str | None
    has_streams: bool
    is_indoor: bool


class ActivityDetail(ActivityListItem):
    description: str | None
    max_hr_bpm: float | None
    max_speed_mps: float | None
    max_power_w: float | None
    avg_cadence_rpm: float | None
    normalized_power_w: float | None
    intensity_factor: float | None
    efficiency_factor: float | None
    decoupling_pct: float | None
    tss: float | None
    trimp: float | None
    calories: float | None
    elevation_loss_m: float | None
    polyline: str | None
    start_lat: float | None
    start_lng: float | None
    available_channels: list[str]
    is_commute: bool
    zone_time: dict[str, dict[int, int]] = Field(default_factory=dict)
    best_efforts: dict[str, dict[int, float]] = Field(default_factory=dict)
    distance_prs: dict[int, float] = Field(default_factory=dict)


class ActivityPage(BaseModel):
    items: list[ActivityListItem]
    total: int
    limit: int
    offset: int


class StreamsResponse(BaseModel):
    activity_id: UUID
    n_samples: int
    channels: dict[str, list[float | None]]


# ---- charts -----------------------------------------------------------------


class ChartMeta(BaseModel):
    """Everything the UI needs to render honestly.

    ``is_estimate`` and ``coverage_note`` exist because a chart built from a biased
    subset ("312 of 1,208 activities have heart rate") must say so rather than
    quietly average over whatever it found (FRONTEND_DESIGN.md).
    """

    chart_id: str
    title: str
    question: str
    unit: str | None = None
    is_estimate: bool = False
    estimate_reason: str | None = None
    coverage_note: str | None = None
    activities_used: int = 0
    activities_total: int = 0


class ChartSeries(BaseModel):
    key: str
    label: str
    points: list[dict[str, Any]]


class ChartResponse(BaseModel):
    meta: ChartMeta
    series: list[ChartSeries]


class StatTile(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    delta_pct: float | None = None
    sparkline: list[float] = Field(default_factory=list)


class DashboardSummary(BaseModel):
    period_label: str
    hero: StatTile
    tiles: list[StatTile]
    streak_days: int
    longest_streak_days: int
    active_days: int
