"""Training-load metrics.

Every function here is pure: numbers in, numbers out. No clock, no DB, no network —
which is what makes them testable against hand-computed values (CLAUDE.md §6).

Formulas are cited inline. If you change one, you recompute history; you do not
migrate the stored numbers (CLAUDE.md §4.3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from sp_core.canonical.sports import SPORT_INTENSITY_FACTOR

LoadSource = Literal["tss", "trimp", "rpe", "duration"]

# Banister TRIMP gender coefficients. We default to the male value and label the
# result estimated when sex is unknown, rather than silently picking one.
_TRIMP_K_MALE = 1.92
_TRIMP_K_FEMALE = 1.67

#: Normalized Power is defined on a 30-second rolling average.
_NP_WINDOW_S = 30


@dataclass(frozen=True, slots=True)
class TrainingLoad:
    """A load value plus which rung of the fallback ladder produced it.

    The source must travel with the number: a TSS-derived CTL and a duration-derived
    CTL are not comparable, and the UI has to say so (FEATURES.md).
    """

    value: float
    source: LoadSource

    @property
    def is_estimate(self) -> bool:
        return self.source in ("rpe", "duration")


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Centred-trailing rolling mean over a fixed sample window.

    NaNs are treated as zero power/effort (a dropout is not a hard effort), which
    matches how head units record signal loss.
    """
    if window <= 1 or len(values) == 0:
        return values.astype(np.float64)
    clean = np.nan_to_num(values.astype(np.float64), nan=0.0)
    cumulative = np.concatenate(([0.0], np.cumsum(clean)))
    n = len(clean)
    starts = np.maximum(np.arange(n) - window + 1, 0)
    ends = np.arange(n) + 1
    return (cumulative[ends] - cumulative[starts]) / (ends - starts)


def normalized_power(power: np.ndarray, sample_rate_hz: float = 1.0) -> float | None:
    """Normalized Power = 4th root of the mean of the 4th power of a 30 s rolling mean.

    Coggan & Allen, *Training and Racing with a Power Meter*.
    """
    if len(power) == 0:
        return None
    finite = power[np.isfinite(power)]
    if len(finite) == 0 or not np.any(finite > 0):
        return None

    window = max(1, round(_NP_WINDOW_S * sample_rate_hz))
    if len(power) < window:
        mean = float(np.nanmean(power))
        return mean if math.isfinite(mean) else None

    smoothed = rolling_mean(power, window)
    # Discard the ramp-up region where the rolling window is not yet full.
    smoothed = smoothed[window - 1 :]
    if len(smoothed) == 0:
        return None
    result = float(np.mean(np.power(smoothed, 4)) ** 0.25)
    return result if math.isfinite(result) else None


def intensity_factor(np_watts: float | None, ftp_w: int | None) -> float | None:
    """IF = NP / FTP."""
    if not np_watts or not ftp_w or ftp_w <= 0:
        return None
    return np_watts / ftp_w


def training_stress_score(
    duration_s: int, np_watts: float | None, ftp_w: int | None
) -> float | None:
    """TSS = (duration_s x NP x IF) / (FTP x 3600) x 100.

    One hour at FTP is 100 TSS by definition, which is the check the tests assert.
    """
    factor = intensity_factor(np_watts, ftp_w)
    if factor is None or np_watts is None or not ftp_w or duration_s <= 0:
        return None
    return (duration_s * np_watts * factor) / (ftp_w * 3600) * 100


def trimp(
    duration_s: int,
    avg_hr_bpm: float | None,
    resting_hr_bpm: int | None,
    max_hr_bpm: int | None,
    sex: str | None = None,
) -> float | None:
    """Banister TRIMP = duration_min x HRr x 0.64 x e^(k x HRr).

    HRr = (HRavg - HRrest) / (HRmax - HRrest), k = 1.92 male / 1.67 female.
    """
    if not avg_hr_bpm or not max_hr_bpm or resting_hr_bpm is None or duration_s <= 0:
        return None
    if max_hr_bpm <= resting_hr_bpm:
        return None

    reserve = (avg_hr_bpm - resting_hr_bpm) / (max_hr_bpm - resting_hr_bpm)
    if reserve <= 0:
        return 0.0
    reserve = min(reserve, 1.5)  # guard against a bad max-HR setting

    k = _TRIMP_K_FEMALE if (sex or "").upper().startswith("F") else _TRIMP_K_MALE
    return (duration_s / 60.0) * reserve * 0.64 * math.exp(k * reserve)


def compute_training_load(
    *,
    duration_s: int,
    sport_group: str,
    np_watts: float | None = None,
    ftp_w: int | None = None,
    avg_hr_bpm: float | None = None,
    resting_hr_bpm: int | None = None,
    max_hr_bpm: int | None = None,
    perceived_exertion: int | None = None,
    sex: str | None = None,
) -> TrainingLoad | None:
    """The fallback ladder (FEATURES.md § metric formulas).

    1. TSS if power + FTP    2. TRIMP if HR + zones
    3. RPE x duration        4. duration x sport factor

    Always returns which rung was used so the UI can label estimates.
    """
    if duration_s <= 0:
        return None

    tss = training_stress_score(duration_s, np_watts, ftp_w)
    if tss is not None:
        return TrainingLoad(tss, "tss")

    banister = trimp(duration_s, avg_hr_bpm, resting_hr_bpm, max_hr_bpm, sex)
    if banister is not None:
        return TrainingLoad(banister, "trimp")

    minutes = duration_s / 60.0
    if perceived_exertion:
        return TrainingLoad(perceived_exertion * minutes, "rpe")

    return TrainingLoad(minutes * SPORT_INTENSITY_FACTOR.get(sport_group, 0.6), "duration")


def efficiency_factor(output: float | None, avg_hr_bpm: float | None) -> float | None:
    """EF = normalized power (or speed) / average heart rate."""
    if not output or not avg_hr_bpm or avg_hr_bpm <= 0:
        return None
    return output / avg_hr_bpm


def aerobic_decoupling(output: np.ndarray, heartrate: np.ndarray) -> float | None:
    """Decoupling % = (EF_first_half - EF_second_half) / EF_first_half x 100.

    The classic aerobic-durability check: if pace-per-heartbeat degrades across an
    otherwise steady effort, aerobic base is the limiter. Positive = degraded.
    """
    if len(output) != len(heartrate) or len(output) < 4:
        return None

    midpoint = len(output) // 2
    halves = []
    for out_half, hr_half in (
        (output[:midpoint], heartrate[:midpoint]),
        (output[midpoint:], heartrate[midpoint:]),
    ):
        valid = np.isfinite(out_half) & np.isfinite(hr_half) & (hr_half > 0)
        if not bool(valid.any()):
            return None
        halves.append(float(np.mean(out_half[valid])) / float(np.mean(hr_half[valid])))

    first, second = halves
    if first <= 0:
        return None
    return (first - second) / first * 100.0
