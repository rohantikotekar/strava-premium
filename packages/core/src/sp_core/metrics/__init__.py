"""Derived metrics for a single activity.

``analyze_activity`` is the one entry point the worker calls. It is pure: given an
activity, its streams, and the athlete's profile, it returns everything we store as
derived data. No DB, no clock, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sp_core.canonical.activity import CanonicalActivity, Channel, StreamSet
from sp_core.canonical.profile import AthleteProfile
from sp_core.metrics.curves import (
    DEFAULT_DURATIONS_S,
    DEFAULT_PR_DISTANCES_M,
    estimate_sample_rate_hz,
    fastest_efforts_by_distance,
    mean_maximal,
)
from sp_core.metrics.fitness import (
    DailyPoint,
    acute_chronic_ratio,
    build_daily_series,
    compute_fitness_series,
    riegel_predict,
)
from sp_core.metrics.load import (
    TrainingLoad,
    aerobic_decoupling,
    compute_training_load,
    efficiency_factor,
    intensity_factor,
    normalized_power,
    training_stress_score,
    trimp,
)
from sp_core.metrics.zones import hr_zones, power_zones

__all__ = [
    "FIELD_CAPABILITIES",
    "ActivityMetrics",
    "AthleteProfile",
    "DailyPoint",
    "TrainingLoad",
    "acute_chronic_ratio",
    "analyze_activity",
    "build_daily_series",
    "capabilities_for",
    "capability_for_channel",
    "compute_fitness_series",
    "compute_training_load",
    "riegel_predict",
    "summarise_streams",
]


@dataclass(slots=True)
class ActivityMetrics:
    """Everything we derive for one activity. Mirrors the aggregate tables."""

    training_load: float | None = None
    load_source: str | None = None
    tss: float | None = None
    trimp: float | None = None
    normalized_power_w: float | None = None
    intensity_factor: float | None = None
    efficiency_factor: float | None = None
    decoupling_pct: float | None = None

    #: {zone_kind: {zone_index: seconds}}
    zone_time: dict[str, dict[int, int]] = field(default_factory=dict)
    #: {metric: {duration_s: value}}
    best_efforts: dict[str, dict[int, float]] = field(default_factory=dict)
    #: {distance_m: seconds}
    distance_prs: dict[int, float] = field(default_factory=dict)

    available_channels: list[str] = field(default_factory=list)


def analyze_activity(
    activity: CanonicalActivity,
    streams: StreamSet | None,
    profile: AthleteProfile,
) -> ActivityMetrics:
    """Compute every derived metric available for this activity.

    Degrades gracefully: an activity with no streams still gets a training load
    from its summary fields, which is what makes the CSV fast path chartable
    before a single .fit file is parsed (INGESTION.md §3).
    """
    metrics = ActivityMetrics()
    duration_s = activity.moving_time_s or activity.elapsed_time_s or 0

    power = streams.get(Channel.POWER) if streams else None
    heartrate = streams.get(Channel.HEARTRATE) if streams else None
    time_s = streams.get(Channel.TIME) if streams else None
    distance = streams.get(Channel.DISTANCE) if streams else None
    speed = streams.get(Channel.SPEED) if streams else None

    if streams is not None:
        metrics.available_channels = [c.value for c in streams.available()]

    sample_rate = estimate_sample_rate_hz(time_s) if time_s is not None else 1.0

    # ---- power-derived -------------------------------------------------------
    if power is not None and streams is not None and streams.has(Channel.POWER):
        metrics.normalized_power_w = normalized_power(power, sample_rate)
    elif activity.weighted_avg_power_w:
        metrics.normalized_power_w = activity.weighted_avg_power_w

    metrics.intensity_factor = intensity_factor(metrics.normalized_power_w, profile.ftp_w)
    metrics.tss = training_stress_score(duration_s, metrics.normalized_power_w, profile.ftp_w)

    # ---- heart-rate-derived --------------------------------------------------
    metrics.trimp = trimp(
        duration_s,
        activity.avg_hr_bpm,
        profile.resting_hr_bpm,
        profile.max_hr_bpm,
        profile.sex,
    )

    load = compute_training_load(
        duration_s=duration_s,
        sport_group=activity.sport_group,
        np_watts=metrics.normalized_power_w,
        ftp_w=profile.ftp_w,
        avg_hr_bpm=activity.avg_hr_bpm,
        resting_hr_bpm=profile.resting_hr_bpm,
        max_hr_bpm=profile.max_hr_bpm,
        perceived_exertion=activity.perceived_exertion,
        sex=profile.sex,
    )
    if load is not None:
        metrics.training_load = load.value
        metrics.load_source = load.source

    # ---- efficiency ----------------------------------------------------------
    output = metrics.normalized_power_w or activity.avg_speed_mps
    metrics.efficiency_factor = efficiency_factor(output, activity.avg_hr_bpm)

    if heartrate is not None and streams is not None and streams.has(Channel.HEARTRATE):
        pace_or_power = power if (power is not None and streams.has(Channel.POWER)) else speed
        if pace_or_power is not None:
            metrics.decoupling_pct = aerobic_decoupling(pace_or_power, heartrate)

    # ---- zones ---------------------------------------------------------------
    if streams is not None and time_s is not None:
        if streams.has(Channel.HEARTRATE) and heartrate is not None and profile.max_hr_bpm:
            metrics.zone_time["hr"] = hr_zones(heartrate, time_s, profile.max_hr_bpm)
        if streams.has(Channel.POWER) and power is not None and profile.ftp_w:
            metrics.zone_time["power"] = power_zones(power, time_s, profile.ftp_w)

    # ---- curves and PRs ------------------------------------------------------
    if streams is not None:
        if streams.has(Channel.POWER) and power is not None:
            curve = mean_maximal(power, DEFAULT_DURATIONS_S, sample_rate)
            if curve:
                metrics.best_efforts["power"] = curve
        if streams.has(Channel.SPEED) and speed is not None:
            curve = mean_maximal(speed, DEFAULT_DURATIONS_S, sample_rate)
            if curve:
                metrics.best_efforts["speed"] = curve
        if streams.has(Channel.HEARTRATE) and heartrate is not None:
            curve = mean_maximal(heartrate, DEFAULT_DURATIONS_S, sample_rate)
            if curve:
                metrics.best_efforts["hr"] = curve

        if distance is not None and time_s is not None and streams.has(Channel.DISTANCE):
            metrics.distance_prs = fastest_efforts_by_distance(
                distance, time_s, DEFAULT_PR_DISTANCES_M
            )

    return metrics


#: Parquet column name -> the capability suffix the chart registry checks against.
#: Deliberately not the raw column name: the frontend declares
#: `requires: ["stream.heartrate"]`, not `["stream.heartrate_bpm"]`.
_CHANNEL_CAPABILITY: dict[Channel, str] = {
    Channel.HEARTRATE: "heartrate",
    Channel.POWER: "power",
    Channel.CADENCE: "cadence",
    Channel.ALTITUDE: "altitude",
    Channel.DISTANCE: "distance",
    Channel.SPEED: "speed",
    Channel.TEMPERATURE: "temperature",
    Channel.LAT: "latlng",
    Channel.LNG: "latlng",
}

_BY_COLUMN: dict[str, str] = {
    channel.value: suffix for channel, suffix in _CHANNEL_CAPABILITY.items()
}


def capability_for_channel(column: str) -> str | None:
    """Capability string for a stored Parquet column name.

    **The single source of truth for stream capability naming.** Both the ingest
    path and the whole-history rebuild must go through here — when they each had
    their own mapping they disagreed (`stream.heartrate_bpm` vs
    `stream.heartrate`) and every stream-gated chart silently vanished.
    """
    if column == Channel.TIME.value:
        return None
    suffix = _BY_COLUMN.get(column)
    return f"stream.{suffix}" if suffix else None


#: Activity attribute -> capability, for data present without a stream.
FIELD_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("avg_power_w", "field.power"),
    ("avg_hr_bpm", "field.heartrate"),
    ("avg_cadence_rpm", "field.cadence"),
    ("gear_external_id", "field.gear"),
    ("perceived_exertion", "field.rpe"),
    ("elevation_gain_m", "field.elevation"),
)


def capabilities_for(activity: CanonicalActivity, streams: StreamSet | None) -> set[str]:
    """Capability strings this activity contributes (CLAUDE.md §5).

    These accumulate into `user_capabilities` and decide which charts the frontend
    renders at all.
    """
    found = {f"sport.{activity.sport_group}"}

    for attribute, capability in FIELD_CAPABILITIES:
        if getattr(activity, attribute, None):
            found.add(capability)

    if streams is not None:
        for channel in streams.available():
            stream_capability = capability_for_channel(channel.value)
            if stream_capability:
                found.add(stream_capability)

    return found


def summarise_streams(streams: StreamSet) -> dict[str, float]:
    """Summary fields recoverable from streams when the source had none (GPX)."""
    out: dict[str, float] = {}
    heartrate = streams.get(Channel.HEARTRATE)
    if heartrate is not None and streams.has(Channel.HEARTRATE):
        valid = heartrate[np.isfinite(heartrate) & (heartrate > 0)]
        if len(valid):
            out["avg_hr_bpm"] = float(valid.mean())
            out["max_hr_bpm"] = float(valid.max())

    power = streams.get(Channel.POWER)
    if power is not None and streams.has(Channel.POWER):
        valid = power[np.isfinite(power)]
        if len(valid):
            out["avg_power_w"] = float(valid.mean())
            out["max_power_w"] = float(valid.max())

    cadence = streams.get(Channel.CADENCE)
    if cadence is not None and streams.has(Channel.CADENCE):
        valid = cadence[np.isfinite(cadence) & (cadence > 0)]
        if len(valid):
            out["avg_cadence_rpm"] = float(valid.mean())

    return out
