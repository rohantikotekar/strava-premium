"""The canonical contract.

Every parser — bulk CSV, FIT, GPX, TCX, Strava API — produces these two models and
nothing downstream knows which format the data came from.

Two rules that matter more than they look:

* **Every measure is Optional and ``None`` means "not measured", never ``0``.**
  A zero heart rate silently corrupts averages and makes charts lie.
* **Unknown fields go in ``extra``, never dropped** (CLAUDE.md §4.7).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

Source = Literal["bulk_csv", "fit", "gpx", "tcx", "strava_api"]


class Channel(StrEnum):
    """Per-sample stream channels. The value is the Parquet column name."""

    TIME = "t"
    LAT = "lat"
    LNG = "lng"
    ALTITUDE = "altitude_m"
    DISTANCE = "distance_m"
    SPEED = "speed_mps"
    HEARTRATE = "heartrate_bpm"
    CADENCE = "cadence_rpm"
    POWER = "power_w"
    TEMPERATURE = "temp_c"
    MOVING = "moving"


class CanonicalActivity(BaseModel):
    """One activity, normalised. Mirrors the `activities` table."""

    model_config = ConfigDict(extra="forbid")

    # identity
    source: Source
    strava_activity_id: int | None = None
    content_hash: str | None = None

    # when
    start_time_utc: datetime
    start_time_local: datetime
    utc_offset_s: int = 0
    elapsed_time_s: int
    moving_time_s: int | None = None

    # what
    sport_type: str
    name: str | None = None
    description: str | None = None
    is_indoor: bool = False
    is_commute: bool = False
    is_race: bool = False
    gear_external_id: str | None = None

    # measures — all Optional by design
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    avg_speed_mps: float | None = None
    max_speed_mps: float | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    avg_cadence_rpm: float | None = None
    avg_power_w: float | None = None
    max_power_w: float | None = None
    weighted_avg_power_w: float | None = None
    kilojoules: float | None = None
    calories: float | None = None
    avg_temp_c: float | None = None
    perceived_exertion: int | None = None
    relative_effort: float | None = None

    # geo
    start_lat: float | None = None
    start_lng: float | None = None
    polyline: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def sport_group(self) -> str:
        from sp_core.canonical.sports import sport_group

        return sport_group(self.sport_type)


class StreamSet(BaseModel):
    """Per-sample channels for one activity. Written to Parquet, never to Postgres."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    channels: dict[Channel, np.ndarray] = Field(default_factory=dict)

    @property
    def n_samples(self) -> int:
        for arr in self.channels.values():
            return len(arr)
        return 0

    def has(self, channel: Channel) -> bool:
        """True only if the channel exists AND carries at least one real value.

        A device that writes an all-zero power column is claiming a capability it
        doesn't have; treating that as "has power" would render an empty chart.
        """
        arr = self.channels.get(channel)
        if arr is None or len(arr) == 0:
            return False
        finite = arr[np.isfinite(arr)] if arr.dtype.kind == "f" else arr
        if len(finite) == 0:
            return False
        return bool(np.any(finite != 0))

    def get(self, channel: Channel) -> np.ndarray | None:
        return self.channels.get(channel)

    def available(self) -> list[Channel]:
        return sorted((c for c in self.channels if self.has(c)), key=lambda c: c.value)


class ParseResult(BaseModel):
    """What every parser returns."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    activity: CanonicalActivity
    streams: StreamSet | None = None
