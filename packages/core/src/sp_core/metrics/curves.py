"""Mean-maximal curves and in-activity distance PRs.

Both are computed in O(n) per target rather than the obvious O(n*d) — a 6-hour ride
at 1 Hz is ~21,600 samples and the naive nested loop over 40 durations is what makes
naive implementations of this take minutes per activity.
"""

from __future__ import annotations

import numpy as np

#: Durations (seconds) the power/pace curve is sampled at. Dense at the short end
#: where the curve bends hardest, sparse in the aerobic tail.
DEFAULT_DURATIONS_S: tuple[int, ...] = (
    1,
    2,
    5,
    10,
    15,
    20,
    30,
    45,
    60,
    90,
    120,
    180,
    240,
    300,
    420,
    600,
    900,
    1200,
    1800,
    2400,
    3600,
    5400,
    7200,
    10800,
)

#: Distances (metres) we look for personal records at.
DEFAULT_PR_DISTANCES_M: tuple[int, ...] = (
    400,
    800,
    1000,
    1609,
    5000,
    10000,
    15000,
    16093,
    21097,
    42195,
)


def mean_maximal(
    values: np.ndarray,
    durations_s: tuple[int, ...] = DEFAULT_DURATIONS_S,
    sample_rate_hz: float = 1.0,
) -> dict[int, float]:
    """Best sustained average of ``values`` for each duration.

    This is the power curve for watts, and the same construction gives a pace curve
    for speed. Uses a prefix sum so each duration costs one vectorised subtraction.
    """
    if len(values) == 0:
        return {}

    clean = np.nan_to_num(values.astype(np.float64), nan=0.0)
    if not np.any(clean > 0):
        return {}

    prefix = np.concatenate(([0.0], np.cumsum(clean)))
    total = len(clean)

    best: dict[int, float] = {}
    for duration in durations_s:
        window = max(1, round(duration * sample_rate_hz))
        if window > total:
            break  # durations are ascending; everything after is longer still
        window_sums = prefix[window:] - prefix[:-window]
        peak = float(window_sums.max() / window)
        if peak > 0:
            best[duration] = peak
    return best


def fastest_efforts_by_distance(
    distance_m: np.ndarray,
    time_s: np.ndarray,
    distances_m: tuple[int, ...] = DEFAULT_PR_DISTANCES_M,
) -> dict[int, float]:
    """Fastest time to cover each target distance anywhere within the activity.

    Two-pointer sweep over the cumulative distance array: O(n) per target distance.
    Returns ``{distance_m: seconds}`` only for distances actually covered.
    """
    if len(distance_m) != len(time_s) or len(distance_m) < 2:
        return {}

    valid = np.isfinite(distance_m) & np.isfinite(time_s)
    distance, time = distance_m[valid], time_s[valid]
    if len(distance) < 2:
        return {}

    # Cumulative distance must be non-decreasing; GPS jitter can violate that.
    distance = np.maximum.accumulate(distance)
    total = float(distance[-1] - distance[0])

    results: dict[int, float] = {}
    for target in distances_m:
        if total < target:
            continue
        best = np.inf
        start = 0
        for end in range(1, len(distance)):
            # Advance the trailing pointer while the window still spans the target,
            # which finds the tightest window ending at `end`.
            while start < end and distance[end] - distance[start + 1] >= target:
                start += 1
            if distance[end] - distance[start] >= target:
                best = min(best, float(time[end] - time[start]))
        if np.isfinite(best) and best > 0:
            results[target] = best
    return results


def estimate_sample_rate_hz(time_s: np.ndarray) -> float:
    """Median sample rate. Robust to pauses, unlike (n / duration)."""
    if len(time_s) < 2:
        return 1.0
    diffs = np.diff(time_s[np.isfinite(time_s)])
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return 1.0
    median_dt = float(np.median(positive))
    return 1.0 / median_dt if median_dt > 0 else 1.0
