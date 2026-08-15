"""Fitness / Fatigue / Form — the CTL, ATL, TSB series.

The single most important implementation detail: **rest days must be present as
zero-load rows**. Exponential decay over a sparse series silently inflates fitness,
because a gap gets treated as "no decay happened" instead of "nothing was trained".
``build_daily_series`` is what guarantees the calendar is dense.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

#: Standard Bannister/TrainingPeaks time constants, in days.
CTL_DAYS = 42
ATL_DAYS = 7


@dataclass(frozen=True, slots=True)
class DailyPoint:
    day: date
    load: float
    ctl: float
    atl: float

    @property
    def tsb(self) -> float:
        """Form = fitness - fatigue."""
        return self.ctl - self.atl


def _alpha(time_constant_days: int) -> float:
    """EWMA smoothing factor for an exponential decay with the given time constant."""
    return 1.0 - math.exp(-1.0 / time_constant_days)


def build_daily_series(
    loads_by_day: Mapping[date, float],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[date, float]]:
    """Expand a sparse {day: load} mapping into a dense, gap-free daily series."""
    if not loads_by_day and (start is None or end is None):
        return []

    first = start or min(loads_by_day)
    last = end or max(loads_by_day)
    if last < first:
        return []

    days = (last - first).days + 1
    return [
        (
            first + timedelta(days=offset),
            float(loads_by_day.get(first + timedelta(days=offset), 0.0)),
        )
        for offset in range(days)
    ]


def compute_fitness_series(
    daily: Iterable[tuple[date, float]],
    *,
    initial_ctl: float = 0.0,
    initial_atl: float = 0.0,
) -> list[DailyPoint]:
    """Run the CTL/ATL exponentially-weighted moving averages over a dense series.

        CTL_today = CTL_yesterday + (load_today - CTL_yesterday) x alpha_42
        ATL_today = ATL_yesterday + (load_today - ATL_yesterday) x alpha_7
        TSB_today = CTL_today - ATL_today

    ``initial_*`` seed the series so an incremental recompute can resume from
    yesterday's stored values instead of replaying the whole history.
    """
    ctl_alpha, atl_alpha = _alpha(CTL_DAYS), _alpha(ATL_DAYS)
    ctl, atl = initial_ctl, initial_atl

    points: list[DailyPoint] = []
    for day, load in daily:
        ctl += (load - ctl) * ctl_alpha
        atl += (load - atl) * atl_alpha
        points.append(DailyPoint(day=day, load=load, ctl=ctl, atl=atl))
    return points


def acute_chronic_ratio(ctl: float, atl: float) -> float | None:
    """ACWR = ATL / CTL. Conventionally 0.8–1.3 is the 'sweet spot'.

    Presented as information, never as medical advice (FEATURES.md v3).
    """
    if ctl <= 0:
        return None
    return atl / ctl


def riegel_predict(
    known_time_s: float, known_distance_m: float, target_distance_m: float
) -> float | None:
    """Riegel race prediction: T2 = T1 x (D2 / D1)^1.06.

    The 1.06 exponent is empirical and degrades badly beyond ~2x extrapolation, so
    callers should show the source effort alongside the prediction.
    """
    if known_time_s <= 0 or known_distance_m <= 0 or target_distance_m <= 0:
        return None
    return float(known_time_s * (target_distance_m / known_distance_m) ** 1.06)
