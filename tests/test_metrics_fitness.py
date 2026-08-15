"""CTL / ATL / TSB and the dense-calendar requirement."""

from __future__ import annotations

import math
from datetime import date

import pytest
from sp_core.metrics.fitness import (
    ATL_DAYS,
    CTL_DAYS,
    acute_chronic_ratio,
    build_daily_series,
    compute_fitness_series,
    riegel_predict,
)

CTL_ALPHA = 1 - math.exp(-1 / CTL_DAYS)  # 0.0235284...
ATL_ALPHA = 1 - math.exp(-1 / ATL_DAYS)  # 0.1331221...


class TestBuildDailySeries:
    def test_fills_rest_days_with_zero(self):
        """The single most important behaviour in this module.

        Exponential decay over a *sparse* series silently inflates fitness: a gap
        reads as "no decay happened" instead of "nothing was trained".
        """
        sparse = {date(2026, 1, 1): 100.0, date(2026, 1, 5): 50.0}
        dense = build_daily_series(sparse)

        assert len(dense) == 5
        assert [load for _, load in dense] == [100.0, 0.0, 0.0, 0.0, 50.0]

    def test_respects_explicit_bounds(self):
        dense = build_daily_series(
            {date(2026, 1, 3): 10.0}, start=date(2026, 1, 1), end=date(2026, 1, 5)
        )
        assert len(dense) == 5
        assert dense[0] == (date(2026, 1, 1), 0.0)
        assert dense[2] == (date(2026, 1, 3), 10.0)

    def test_empty_input(self):
        assert build_daily_series({}) == []


class TestFitnessSeries:
    def test_first_day_hand_computed(self):
        """alpha_42 = 1 - e^(-1/42) = 0.02352831328
        alpha_7  = 1 - e^(-1/7)  = 0.13312210001

        CTL_1 = 0 + (100 - 0) x alpha_42 = 2.352831328
        ATL_1 = 0 + (100 - 0) x alpha_7  = 13.312210001
        """
        points = compute_fitness_series([(date(2026, 1, 1), 100.0)])
        assert points[0].ctl == pytest.approx(100 * CTL_ALPHA)
        assert points[0].ctl == pytest.approx(2.3528313, abs=1e-6)
        assert points[0].atl == pytest.approx(100 * ATL_ALPHA)
        assert points[0].atl == pytest.approx(13.3122100, abs=1e-6)

    def test_second_day_hand_computed(self):
        """alpha_42 = 1 - e^(-1/42) = 0.0235283

        CTL_1 = 100 x 0.0235283                        = 2.3528326
        CTL_2 = 2.3528326 + (100 - 2.3528326) x alpha  = 4.6503045
        """
        points = compute_fitness_series([(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 100.0)])
        expected = 100 * CTL_ALPHA
        expected = expected + (100 - expected) * CTL_ALPHA
        assert points[1].ctl == pytest.approx(expected)
        assert points[1].ctl == pytest.approx(4.6503045, abs=1e-6)

    def test_tsb_is_ctl_minus_atl(self):
        points = compute_fitness_series([(date(2026, 1, 1), 100.0)])
        assert points[0].tsb == pytest.approx(points[0].ctl - points[0].atl)

    def test_rest_days_decay_fitness(self):
        """After a hard day, fitness must fall on every rest day."""
        series = [(date(2026, 1, 1), 100.0)] + [(date(2026, 1, day), 0.0) for day in range(2, 11)]
        points = compute_fitness_series(series)
        ctls = [p.ctl for p in points]
        assert ctls == sorted(ctls, reverse=True)
        assert ctls[-1] < ctls[0]

    def test_atl_decays_faster_than_ctl(self):
        """Fatigue is a 7-day constant, fitness a 42-day one — that difference is
        the entire point of the model."""
        series = [(date(2026, 1, 1), 100.0)] + [(date(2026, 1, day), 0.0) for day in range(2, 15)]
        points = compute_fitness_series(series)
        first, last = points[0], points[-1]
        assert (last.atl / first.atl) < (last.ctl / first.ctl)

    def test_steady_load_converges_toward_that_load(self):
        [(date(2026, 1, 1), 50.0)] * 400
        points = compute_fitness_series([(date(2026, 1, 1), 50.0) for _ in range(400)])
        assert points[-1].ctl == pytest.approx(50.0, abs=0.5)

    def test_seeded_resume_matches_full_replay(self):
        """An incremental recompute must equal replaying the whole history."""
        full = compute_fitness_series([(date(2026, 1, d), 100.0) for d in range(1, 11)])
        first_half = compute_fitness_series([(date(2026, 1, d), 100.0) for d in range(1, 6)])
        resumed = compute_fitness_series(
            [(date(2026, 1, d), 100.0) for d in range(6, 11)],
            initial_ctl=first_half[-1].ctl,
            initial_atl=first_half[-1].atl,
        )
        assert resumed[-1].ctl == pytest.approx(full[-1].ctl)
        assert resumed[-1].atl == pytest.approx(full[-1].atl)


class TestAcuteChronicRatio:
    def test_ratio(self):
        assert acute_chronic_ratio(50.0, 60.0) == pytest.approx(1.2)

    def test_none_when_no_chronic_base(self):
        assert acute_chronic_ratio(0.0, 10.0) is None


class TestRiegel:
    def test_marathon_from_half_hand_computed(self):
        """T2 = T1 x (D2/D1)^1.06.

        A 90-minute half (5400 s) predicts:
          5400 x 2^1.06 = 5400 x 2.0851 = 11259.6 s ~ 3:07:40
        """
        result = riegel_predict(5400, 21097.5, 42195)
        assert result is not None
        assert result == pytest.approx(5400 * 2**1.06, rel=1e-9)
        assert result == pytest.approx(11259.6, abs=1.0)

    def test_same_distance_returns_same_time(self):
        assert riegel_predict(1200, 5000, 5000) == pytest.approx(1200)

    def test_rejects_nonsense(self):
        assert riegel_predict(0, 5000, 10000) is None
        assert riegel_predict(1200, 0, 10000) is None
