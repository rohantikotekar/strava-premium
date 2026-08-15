"""Mean-maximal curves, distance PRs, and zone accounting."""

from __future__ import annotations

import numpy as np
import pytest
from sp_core.metrics.curves import (
    estimate_sample_rate_hz,
    fastest_efforts_by_distance,
    mean_maximal,
)
from sp_core.metrics.zones import (
    HR_ZONE_BOUNDS,
    classify,
    estimate_max_hr,
    hr_zones,
    sample_durations,
    time_in_zones,
    zone_boundaries,
)


class TestMeanMaximal:
    def test_one_second_peak_is_the_maximum_sample(self):
        values = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0])
        assert mean_maximal(values, (1,), 1.0)[1] == pytest.approx(5.0)

    def test_two_second_window_hand_computed(self):
        """Windows of 2 over [1,2,3,4,5]: sums 3,5,7,9 -> best mean = 9/2 = 4.5"""
        values = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0])
        assert mean_maximal(values, (2,), 1.0)[2] == pytest.approx(4.5)

    def test_finds_the_peak_wherever_it_sits(self):
        # A 10-sample surge at 400 W buried in a 300-sample ride at 100 W.
        values = np.full(300, 100.0)
        values[150:160] = 400.0
        curve = mean_maximal(values, (5, 10, 60), 1.0)
        assert curve[5] == pytest.approx(400.0)
        assert curve[10] == pytest.approx(400.0)
        # Over 60 s the surge is diluted: (10*400 + 50*100) / 60 = 150
        assert curve[60] == pytest.approx(150.0)

    def test_curve_is_monotonically_non_increasing(self):
        """A longer duration can never sustain more than a shorter one."""
        rng = np.random.default_rng(42)
        values = rng.uniform(50, 400, size=3600)
        curve = mean_maximal(values, (1, 5, 60, 300, 1200, 3600), 1.0)
        ordered = [curve[d] for d in sorted(curve)]
        assert ordered == sorted(ordered, reverse=True)

    def test_skips_durations_longer_than_the_activity(self):
        values = np.full(100, 200.0)
        curve = mean_maximal(values, (1, 60, 3600), 1.0)
        assert 3600 not in curve
        assert 60 in curve

    def test_empty_and_all_zero(self):
        assert mean_maximal(np.asarray([]), (1,), 1.0) == {}
        assert mean_maximal(np.zeros(100), (1,), 1.0) == {}

    def test_respects_sample_rate(self):
        """At 4 Hz a '10 second' window is 40 samples, not 10."""
        values = np.full(400, 200.0)
        # 100 s x 4 Hz = 400 samples: exactly one window fits.
        assert mean_maximal(values, (100,), 4.0)[100] == pytest.approx(200.0)
        # 101 s needs 404 samples — longer than the activity.
        assert mean_maximal(values, (101,), 4.0) == {}
        assert 10 in mean_maximal(values, (10,), 4.0)


class TestFastestEffortsByDistance:
    def test_even_pace(self):
        distance = np.asarray([0.0, 100.0, 200.0, 300.0, 400.0])
        time = np.asarray([0.0, 10.0, 20.0, 30.0, 40.0])
        assert fastest_efforts_by_distance(distance, time, (200,))[200] == pytest.approx(20.0)

    def test_finds_the_fastest_window_not_the_first(self):
        """Fast start then a slow finish: the PR is the opening 200 m at 20 s.

        distance 0,100,200,250,350 / time 0,10,20,40,50
        windows spanning 200 m: (0->2)=20 s, (0->3)=40 s, (1->4)=40 s  => 20 s
        """
        distance = np.asarray([0.0, 100.0, 200.0, 250.0, 350.0])
        time = np.asarray([0.0, 10.0, 20.0, 40.0, 50.0])
        assert fastest_efforts_by_distance(distance, time, (200,))[200] == pytest.approx(20.0)

    def test_omits_distances_never_covered(self):
        distance = np.asarray([0.0, 500.0, 1000.0])
        time = np.asarray([0.0, 150.0, 300.0])
        result = fastest_efforts_by_distance(distance, time, (400, 1000, 5000))
        assert 5000 not in result
        assert 1000 in result

    def test_tolerates_gps_jitter_going_backwards(self):
        """Cumulative distance must never decrease; a jitter sample must not
        produce a negative split."""
        distance = np.asarray([0.0, 100.0, 95.0, 200.0, 300.0])
        time = np.asarray([0.0, 10.0, 20.0, 30.0, 40.0])
        result = fastest_efforts_by_distance(distance, time, (200,))
        assert result[200] > 0

    def test_handles_too_short_input(self):
        assert fastest_efforts_by_distance(np.asarray([0.0]), np.asarray([0.0]), (400,)) == {}


class TestSampleRate:
    def test_one_hz(self):
        assert estimate_sample_rate_hz(np.arange(100, dtype=float)) == pytest.approx(1.0)

    def test_four_hz(self):
        assert estimate_sample_rate_hz(np.arange(0, 100, 0.25)) == pytest.approx(4.0)

    def test_median_ignores_a_long_pause(self):
        """A coffee stop must not halve the apparent sample rate."""
        times = np.concatenate([np.arange(0, 50, 1.0), np.arange(650, 700, 1.0)])
        assert estimate_sample_rate_hz(times) == pytest.approx(1.0)


class TestZones:
    def test_boundaries_from_max_hr(self):
        assert zone_boundaries(200.0, HR_ZONE_BOUNDS) == [120.0, 140.0, 160.0, 180.0]

    def test_classify_is_one_based_and_upper_open(self):
        boundaries = [120.0, 140.0, 160.0, 180.0]
        assert classify(100, boundaries) == 1
        assert classify(130, boundaries) == 2
        assert classify(150, boundaries) == 3
        assert classify(170, boundaries) == 4
        assert classify(190, boundaries) == 5
        assert classify(180, boundaries) == 5  # boundary belongs to the higher zone

    def test_time_in_zones_hand_computed(self):
        """max_hr 200 -> boundaries [120,140,160,180].
        samples 100 (Z1), 150 (Z3), 180 (Z5), 200 (Z5) at 1 s each.
        """
        values = np.asarray([100.0, 150.0, 180.0, 200.0])
        times = np.asarray([0.0, 1.0, 2.0, 3.0])
        result = hr_zones(values, times, 200)
        assert result == {1: 1, 2: 0, 3: 1, 4: 0, 5: 2}

    def test_every_zone_present_even_when_empty(self):
        """Stable legend across activities requires all five keys."""
        values = np.full(10, 100.0)
        times = np.arange(10, dtype=float)
        assert sorted(hr_zones(values, times, 200)) == [1, 2, 3, 4, 5]

    def test_pause_does_not_become_zone_time(self):
        """A device left recording overnight must not add 8 hours to zone 1."""
        values = np.asarray([100.0, 100.0, 100.0])
        times = np.asarray([0.0, 1.0, 30000.0])  # a ~8 hour gap
        total = sum(hr_zones(values, times, 200).values())
        assert total < 200  # clamped, not 30,000 seconds

    def test_zero_and_missing_samples_are_excluded(self):
        values = np.asarray([0.0, np.nan, 150.0])
        times = np.asarray([0.0, 1.0, 2.0])
        result = hr_zones(values, times, 200)
        assert sum(result.values()) == 1

    def test_no_threshold_returns_empty_zones(self):
        values = np.asarray([150.0])
        assert sum(time_in_zones(values, np.asarray([0.0]), 0, HR_ZONE_BOUNDS).values()) == 0

    def test_tanaka_max_hr_estimate(self):
        """HRmax = 208 - 0.7 x age. At 40: 208 - 28 = 180."""
        assert estimate_max_hr(40) == 180

    def test_sample_durations_mirror_last_interval(self):
        durations = sample_durations(np.asarray([0.0, 1.0, 2.0]))
        assert len(durations) == 3
        assert durations.tolist() == [1.0, 1.0, 1.0]
