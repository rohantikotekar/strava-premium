"""Training-load metrics.

Every expected value here is hand-computed from the cited formula, never copied
from what the code currently outputs (CLAUDE.md §6).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from sp_core.metrics.load import (
    aerobic_decoupling,
    compute_training_load,
    efficiency_factor,
    intensity_factor,
    normalized_power,
    rolling_mean,
    training_stress_score,
    trimp,
)


class TestTrainingStressScore:
    def test_one_hour_at_ftp_is_exactly_100_tss(self):
        """TSS = (t x NP x IF) / (FTP x 3600) x 100.

        One hour at threshold is 100 TSS by definition — this is the anchor the
        whole scale is built on.
        """
        assert training_stress_score(3600, 250.0, 250) == pytest.approx(100.0)

    def test_half_hour_at_80_percent(self):
        # IF = 200/250 = 0.8
        # TSS = (1800 * 200 * 0.8) / (250 * 3600) * 100 = 288000 / 900000 * 100 = 32
        assert training_stress_score(1800, 200.0, 250) == pytest.approx(32.0)

    def test_two_hours_at_ftp_is_200(self):
        assert training_stress_score(7200, 250.0, 250) == pytest.approx(200.0)

    @pytest.mark.parametrize(
        ("duration", "np_watts", "ftp"),
        [(3600, None, 250), (3600, 250.0, None), (3600, 250.0, 0), (0, 250.0, 250)],
    )
    def test_returns_none_without_the_inputs(self, duration, np_watts, ftp):
        assert training_stress_score(duration, np_watts, ftp) is None


class TestIntensityFactor:
    def test_if_is_np_over_ftp(self):
        assert intensity_factor(200.0, 250) == pytest.approx(0.8)

    def test_none_without_ftp(self):
        assert intensity_factor(200.0, None) is None


class TestNormalizedPower:
    def test_constant_power_equals_its_own_mean(self):
        """With no variability, NP collapses to average power."""
        power = np.full(600, 200.0)
        assert normalized_power(power, 1.0) == pytest.approx(200.0, rel=1e-6)

    def test_variable_power_exceeds_average(self):
        """The 4th-power weighting is the whole point: surging costs more than its
        arithmetic mean suggests."""
        # 300 s at 100 W then 300 s at 300 W. Mean = 200 W.
        power = np.concatenate([np.full(300, 100.0), np.full(300, 300.0)])
        result = normalized_power(power, 1.0)
        assert result is not None
        assert result > 200.0

    def test_returns_none_for_all_zero_power(self):
        """A device writing an all-zero power column has no power meter."""
        assert normalized_power(np.zeros(600), 1.0) is None

    def test_returns_none_for_empty(self):
        assert normalized_power(np.asarray([]), 1.0) is None


class TestRollingMean:
    def test_trailing_window_over_short_series(self):
        values = np.asarray([1.0, 2.0, 3.0, 4.0])
        # window 2: [1/1, (1+2)/2, (2+3)/2, (3+4)/2]
        assert rolling_mean(values, 2).tolist() == pytest.approx([1.0, 1.5, 2.5, 3.5])

    def test_window_of_one_is_identity(self):
        values = np.asarray([5.0, 7.0])
        assert rolling_mean(values, 1).tolist() == [5.0, 7.0]


class TestTrimp:
    def test_banister_hand_computed(self):
        """TRIMP = min x HRr x 0.64 x e^(1.92 x HRr), HRr = (150-50)/(200-50) = 2/3.

        60 x 0.666667 x 0.64 x e^1.28
          = 40.0 x 0.64 x 3.5966397
          = 25.6 x 3.5966397
          = 92.0740
        """
        expected = 60 * (2 / 3) * 0.64 * math.exp(1.92 * (2 / 3))
        assert trimp(3600, 150.0, 50, 200) == pytest.approx(expected)
        assert trimp(3600, 150.0, 50, 200) == pytest.approx(92.074, abs=0.01)

    def test_female_coefficient_is_lower(self):
        male = trimp(3600, 150.0, 50, 200, sex="M")
        female = trimp(3600, 150.0, 50, 200, sex="F")
        assert male is not None and female is not None
        assert female < male

    def test_hr_at_rest_is_zero_load(self):
        assert trimp(3600, 50.0, 50, 200) == 0.0

    def test_none_without_hr_data(self):
        assert trimp(3600, None, 50, 200) is None
        assert trimp(3600, 150.0, None, 200) is None
        assert trimp(3600, 150.0, 50, None) is None

    def test_none_when_max_hr_below_resting(self):
        """A misconfigured profile must produce nothing, not a negative load."""
        assert trimp(3600, 150.0, 200, 100) is None


class TestFallbackLadder:
    """The ladder must always report which rung it used — a TSS-derived CTL and a
    duration-derived CTL are not comparable (FEATURES.md)."""

    def test_prefers_tss_when_power_available(self):
        load = compute_training_load(
            duration_s=3600,
            sport_group="ride",
            np_watts=250.0,
            ftp_w=250,
            avg_hr_bpm=150.0,
            resting_hr_bpm=50,
            max_hr_bpm=200,
        )
        assert load is not None
        assert load.source == "tss"
        assert load.value == pytest.approx(100.0)
        assert load.is_estimate is False

    def test_falls_back_to_trimp_without_power(self):
        load = compute_training_load(
            duration_s=3600,
            sport_group="run",
            avg_hr_bpm=150.0,
            resting_hr_bpm=50,
            max_hr_bpm=200,
        )
        assert load is not None
        assert load.source == "trimp"
        assert load.is_estimate is False

    def test_falls_back_to_rpe(self):
        load = compute_training_load(duration_s=3600, sport_group="gym", perceived_exertion=7)
        assert load is not None
        assert load.source == "rpe"
        assert load.value == pytest.approx(7 * 60)
        assert load.is_estimate is True

    def test_final_rung_is_duration_times_sport_factor(self):
        load = compute_training_load(duration_s=3600, sport_group="walk")
        assert load is not None
        assert load.source == "duration"
        assert load.value == pytest.approx(60 * 0.4)  # walk factor
        assert load.is_estimate is True

    def test_zero_duration_yields_nothing(self):
        assert compute_training_load(duration_s=0, sport_group="run") is None


class TestEfficiencyAndDecoupling:
    def test_efficiency_factor(self):
        assert efficiency_factor(200.0, 160.0) == pytest.approx(1.25)

    def test_none_without_hr(self):
        assert efficiency_factor(200.0, None) is None

    def test_decoupling_is_zero_for_steady_effort(self):
        output = np.full(100, 200.0)
        heartrate = np.full(100, 150.0)
        assert aerobic_decoupling(output, heartrate) == pytest.approx(0.0)

    def test_positive_decoupling_when_hr_drifts_up(self):
        """Same power, higher heart rate in the second half = aerobic decoupling."""
        output = np.full(100, 200.0)
        heartrate = np.concatenate([np.full(50, 150.0), np.full(50, 165.0)])
        result = aerobic_decoupling(output, heartrate)
        assert result is not None
        # EF1 = 200/150 = 1.3333, EF2 = 200/165 = 1.2121
        # (1.3333 - 1.2121) / 1.3333 * 100 = 9.09%
        assert result == pytest.approx(9.0909, abs=0.01)

    def test_none_for_mismatched_lengths(self):
        assert aerobic_decoupling(np.zeros(10), np.zeros(5)) is None
