"""Heart-rate and power zone models.

Zones are **ordered**, not categorical — which is why the frontend paints them with
a sequential ramp rather than five different hues (FRONTEND_DESIGN.md rule 6).
"""

from __future__ import annotations

import numpy as np

#: Five-zone HR model as fractions of maximum heart rate. The widely-used
#: BCF/Coggan-style split; upper bound of Z5 is open.
HR_ZONE_BOUNDS: tuple[float, ...] = (0.60, 0.70, 0.80, 0.90)

#: Seven-zone Coggan power model as fractions of FTP.
POWER_ZONE_BOUNDS: tuple[float, ...] = (0.55, 0.75, 0.90, 1.05, 1.20, 1.50)

HR_ZONE_LABELS = ("Recovery", "Endurance", "Tempo", "Threshold", "VO2 Max")
POWER_ZONE_LABELS = (
    "Active Recovery",
    "Endurance",
    "Tempo",
    "Threshold",
    "VO2 Max",
    "Anaerobic",
    "Neuromuscular",
)

#: A gap longer than this between samples is a pause, not time spent in a zone.
#: Without this clamp a device left recording overnight adds 8 hours to zone 1.
_MAX_SAMPLE_GAP_S = 60.0


def zone_boundaries(threshold: float, bounds: tuple[float, ...]) -> list[float]:
    """Absolute zone upper bounds (bpm or watts) for a given threshold value."""
    return [threshold * fraction for fraction in bounds]


def classify(value: float, boundaries: list[float]) -> int:
    """1-based zone index for a value against ascending upper bounds."""
    for index, upper in enumerate(boundaries, start=1):
        if value < upper:
            return index
    return len(boundaries) + 1


def sample_durations(time_s: np.ndarray) -> np.ndarray:
    """Seconds each sample represents, clamped so pauses don't become zone time."""
    if len(time_s) == 0:
        return np.asarray([], dtype=np.float64)
    if len(time_s) == 1:
        return np.asarray([1.0], dtype=np.float64)

    diffs = np.diff(time_s)
    diffs = np.where(np.isfinite(diffs) & (diffs > 0), diffs, 0.0)
    diffs = np.minimum(diffs, _MAX_SAMPLE_GAP_S)
    # Each sample covers the interval up to the next; the last mirrors the previous.
    return np.concatenate((diffs, [diffs[-1] if len(diffs) else 1.0]))


def time_in_zones(
    values: np.ndarray,
    time_s: np.ndarray,
    threshold: float,
    bounds: tuple[float, ...],
) -> dict[int, int]:
    """Seconds spent in each zone. Returns ``{zone_index: seconds}``, 1-based.

    Zones with zero time are included so the chart's stacked bar has every segment
    and the legend is stable across activities.
    """
    zone_count = len(bounds) + 1
    empty = dict.fromkeys(range(1, zone_count + 1), 0)

    if len(values) == 0 or threshold <= 0:
        return empty
    if len(time_s) != len(values):
        time_s = np.arange(len(values), dtype=np.float64)

    durations = sample_durations(time_s)
    boundaries = zone_boundaries(threshold, bounds)

    valid = np.isfinite(values) & (values > 0)
    if not bool(valid.any()):
        return empty

    # np.searchsorted gives the zone index directly and vectorises the whole sweep.
    indices = np.searchsorted(np.asarray(boundaries), values[valid], side="right") + 1
    seconds = durations[valid]

    totals = empty.copy()
    for zone in range(1, zone_count + 1):
        totals[zone] = round(float(seconds[indices == zone].sum()))
    return totals


def hr_zones(values: np.ndarray, time_s: np.ndarray, max_hr_bpm: int) -> dict[int, int]:
    return time_in_zones(values, time_s, float(max_hr_bpm), HR_ZONE_BOUNDS)


def power_zones(values: np.ndarray, time_s: np.ndarray, ftp_w: int) -> dict[int, int]:
    return time_in_zones(values, time_s, float(ftp_w), POWER_ZONE_BOUNDS)


def estimate_max_hr(age_years: int) -> int:
    """Tanaka: HRmax = 208 - 0.7 x age. More accurate than 220 - age, especially
    for older athletes. Only ever a fallback — labelled as an estimate in the UI."""
    return round(208 - 0.7 * age_years)
