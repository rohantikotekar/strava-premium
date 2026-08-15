"""Parquet round-trips, polyline coding, capability detection, and password rules."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from sp_core.canonical.activity import CanonicalActivity, Channel, StreamSet
from sp_core.canonical.profile import AthleteProfile
from sp_core.geo import polyline as polyline_codec
from sp_core.metrics import analyze_activity, capabilities_for, capability_for_channel
from sp_core.security.passwords import (
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from sp_core.security.tokens import generate_token, hash_token, tokens_equal
from sp_core.storage.parquet import downsample, parquet_to_streams, streams_to_parquet


def _streams(n: int = 100, with_power: bool = True) -> StreamSet:
    channels = {
        Channel.TIME: np.arange(n, dtype=np.float64),
        Channel.HEARTRATE: np.full(n, 150.0),
        Channel.LAT: np.linspace(51.5, 51.6, n),
        Channel.LNG: np.linspace(-0.12, -0.10, n),
        Channel.DISTANCE: np.arange(n, dtype=np.float64) * 3.0,
        Channel.SPEED: np.full(n, 3.0),
    }
    if with_power:
        channels[Channel.POWER] = np.full(n, 220.0)
    return StreamSet(channels=channels)


class TestParquetRoundTrip:
    def test_round_trip_preserves_values(self):
        original = _streams(50)
        restored = parquet_to_streams(streams_to_parquet(original))

        assert restored.n_samples == 50
        assert restored.has(Channel.HEARTRATE)
        np.testing.assert_allclose(restored.get(Channel.HEARTRATE), original.get(Channel.HEARTRATE))
        np.testing.assert_allclose(restored.get(Channel.LAT), original.get(Channel.LAT), rtol=1e-9)

    def test_absent_channels_are_not_written(self):
        """Only channels carrying real data earn a column."""
        original = _streams(20, with_power=False)
        restored = parquet_to_streams(streams_to_parquet(original))
        assert not restored.has(Channel.POWER)

    def test_all_zero_channel_is_dropped(self):
        """A device writing an all-zero power column is claiming a capability it
        does not have — storing it would render an empty chart."""
        streams = StreamSet(
            channels={
                Channel.TIME: np.arange(10, dtype=np.float64),
                Channel.POWER: np.zeros(10),
            }
        )
        restored = parquet_to_streams(streams_to_parquet(streams))
        assert not restored.has(Channel.POWER)

    def test_empty_streams_serialise_to_nothing(self):
        assert streams_to_parquet(StreamSet()) == b""
        assert parquet_to_streams(b"").n_samples == 0

    def test_compression_is_meaningfully_small(self):
        """The whole ARCHITECTURE.md §3 argument rests on this being tiny.

        A 1-hour activity with 7 channels should land in the documented 40–90 KB
        band. The same data as narrow SQL rows (25,200 rows with indexes) would be
        well over a megabyte.
        """
        payload = streams_to_parquet(_streams(3600))
        assert len(payload) < 100_000

    def test_out_of_range_values_are_clamped_not_corrupted(self):
        """A GPS/sensor glitch writing 9000 bpm must not overflow the uint8 column."""
        streams = StreamSet(
            channels={
                Channel.TIME: np.arange(3, dtype=np.float64),
                Channel.HEARTRATE: np.asarray([150.0, 9000.0, 140.0]),
            }
        )
        restored = parquet_to_streams(streams_to_parquet(streams))
        values = restored.get(Channel.HEARTRATE)
        assert values.max() <= 255


class TestDownsample:
    def test_leaves_short_series_alone(self):
        streams = _streams(100)
        assert downsample(streams, 2000).n_samples == 100

    def test_reduces_long_series(self):
        streams = _streams(20000)
        reduced = downsample(streams, 500)
        assert reduced.n_samples <= 1100  # buckets plus preserved peaks
        assert reduced.n_samples < 20000

    def test_preserves_peaks(self):
        """A 30-second power spike must survive being drawn 800 px wide."""
        n = 10000
        power = np.full(n, 100.0)
        power[5000:5030] = 900.0
        streams = StreamSet(
            channels={Channel.TIME: np.arange(n, dtype=np.float64), Channel.POWER: power}
        )
        reduced = downsample(streams, 200)
        assert reduced.get(Channel.POWER).max() == pytest.approx(900.0)

    def test_all_channels_stay_the_same_length(self):
        reduced = downsample(_streams(5000), 300)
        lengths = {len(values) for values in reduced.channels.values()}
        assert len(lengths) == 1


class TestPolyline:
    def test_round_trip(self):
        points = [(51.5, -0.12), (51.501, -0.121), (51.502, -0.122)]
        decoded = polyline_codec.decode(polyline_codec.encode(points))
        for (lat_a, lng_a), (lat_b, lng_b) in zip(points, decoded, strict=True):
            assert lat_a == pytest.approx(lat_b, abs=1e-5)
            assert lng_a == pytest.approx(lng_b, abs=1e-5)

    def test_known_google_example(self):
        """The reference example from Google's polyline algorithm documentation."""
        encoded = polyline_codec.encode([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])
        assert encoded == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

    def test_simplify_caps_point_count(self):
        points = [(51.5 + i * 1e-5, -0.12) for i in range(5000)]
        assert len(polyline_codec.simplify(points, 500)) <= 500

    def test_from_streams_skips_null_island(self):
        """An all-zero GPS track is a device fault, not a trip to (0, 0)."""
        lat = np.zeros(10)
        lng = np.zeros(10)
        assert polyline_codec.from_streams(lat, lng) is None

    def test_from_streams_ignores_nan(self):
        lat = np.asarray([51.5, np.nan, 51.6])
        lng = np.asarray([-0.12, np.nan, -0.10])
        encoded = polyline_codec.from_streams(lat, lng)
        assert encoded is not None
        assert len(polyline_codec.decode(encoded)) == 2


class TestCapabilityDetection:
    def _activity(self, **kwargs) -> CanonicalActivity:
        defaults = {
            "source": "fit",
            "start_time_utc": datetime(2026, 3, 14, tzinfo=UTC),
            "start_time_local": datetime(2026, 3, 14),
            "elapsed_time_s": 3600,
            "sport_type": "Run",
        }
        return CanonicalActivity(**{**defaults, **kwargs})

    def test_reports_sport_and_streams(self):
        found = capabilities_for(self._activity(), _streams(50))
        assert "sport.run" in found
        assert "stream.heartrate" in found
        assert "stream.power" in found
        assert "stream.latlng" in found

    def test_no_power_means_no_power_capability(self):
        """This is what stops a power-curve chart rendering for a phone-only
        runner (CLAUDE.md §5)."""
        found = capabilities_for(self._activity(), _streams(50, with_power=False))
        assert "stream.power" not in found

    def test_summary_only_activity_still_reports_fields(self):
        found = capabilities_for(self._activity(avg_hr_bpm=150.0), None)
        assert "field.heartrate" in found
        assert "stream.heartrate" not in found

    def test_capability_names_match_what_the_chart_registry_asks_for(self):
        """Regression: the ingest path and the whole-history rebuild once had
        separate mappings and produced `stream.heartrate_bpm` in one and
        `stream.heartrate` in the other, so every stream-gated chart silently
        vanished after a rebuild. Both now go through capability_for_channel.
        """
        # These strings are what apps/web chart registry `requires` declares.
        assert capability_for_channel("heartrate_bpm") == "stream.heartrate"
        assert capability_for_channel("power_w") == "stream.power"
        assert capability_for_channel("speed_mps") == "stream.speed"
        assert capability_for_channel("distance_m") == "stream.distance"
        assert capability_for_channel("lat") == "stream.latlng"
        assert capability_for_channel("lng") == "stream.latlng"
        # Time is structural, not a capability.
        assert capability_for_channel("t") is None
        assert capability_for_channel("nonsense") is None

    def test_both_capability_paths_agree(self):
        """capabilities_for() and the column-name mapping must produce the same
        strings for the same data."""
        streams = _streams(20)
        found = capabilities_for(self._activity(), streams)
        from_activity = {c for c in found if c.startswith("stream.")}
        from_columns = {
            capability_for_channel(channel.value)
            for channel in streams.available()
            if capability_for_channel(channel.value)
        }
        assert from_activity == from_columns


class TestAnalyzeActivity:
    def _activity(self, **kwargs) -> CanonicalActivity:
        defaults = {
            "source": "fit",
            "start_time_utc": datetime(2026, 3, 14, tzinfo=UTC),
            "start_time_local": datetime(2026, 3, 14),
            "elapsed_time_s": 3600,
            "moving_time_s": 3600,
            "sport_type": "Ride",
        }
        return CanonicalActivity(**{**defaults, **kwargs})

    def test_full_analysis_with_power_and_hr(self):
        profile = AthleteProfile(ftp_w=220, max_hr_bpm=190, resting_hr_bpm=50)
        metrics = analyze_activity(self._activity(avg_hr_bpm=150.0), _streams(3600), profile)

        assert metrics.load_source == "tss"
        assert metrics.tss is not None
        assert metrics.normalized_power_w == pytest.approx(220.0, rel=0.01)
        assert "power" in metrics.best_efforts
        assert "hr" in metrics.zone_time

    def test_summary_only_activity_still_gets_a_load(self):
        """The CSV fast path has no streams and must still produce a chartable
        training load — that is what makes the dashboard live in 20 s."""
        profile = AthleteProfile(max_hr_bpm=190, resting_hr_bpm=50)
        metrics = analyze_activity(self._activity(avg_hr_bpm=150.0), None, profile)

        assert metrics.training_load is not None
        assert metrics.load_source == "trimp"

    def test_no_profile_falls_back_to_duration(self):
        metrics = analyze_activity(self._activity(), None, AthleteProfile())
        assert metrics.load_source == "duration"
        assert metrics.training_load is not None


class TestPasswords:
    def test_hash_and_verify(self):
        hashed = hash_password("a-perfectly-fine-passphrase")
        assert verify_password("a-perfectly-fine-passphrase", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_hash_is_salted(self):
        assert hash_password("same-password-here") != hash_password("same-password-here")

    def test_hash_is_argon2id(self):
        assert hash_password("some-password-here").startswith("$argon2id$")

    def test_missing_hash_still_burns_a_verify(self):
        """Timing must not distinguish 'no such user' from 'wrong password'."""
        assert verify_password("anything", None) is False

    def test_needs_rehash_for_garbage(self):
        assert needs_rehash("not-a-hash") is True

    @pytest.mark.parametrize(
        ("password", "ok"),
        [
            ("correct-horse-battery", True),
            ("short", False),  # under 12 chars
            ("aaaaaaaaaaaaaaaa", False),  # too repetitive
            ("x" * 300, False),  # over the max
        ],
    )
    def test_policy(self, password, ok):
        assert validate_password(password).ok is ok

    def test_password_cannot_be_the_email(self):
        assert validate_password("rider@example.com", email="rider@example.com").ok is False

    def test_no_composition_rules(self):
        """NIST 800-63B: length beats forced upper/digit/symbol requirements."""
        assert validate_password("all lowercase words here").ok is True


class TestTokens:
    def test_tokens_are_unique_and_long(self):
        first, second = generate_token(), generate_token()
        assert first != second
        assert len(first) > 30

    def test_hash_is_stable_and_one_way(self):
        token = generate_token()
        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token)

    def test_constant_time_compare(self):
        assert tokens_equal("abc", "abc") is True
        assert tokens_equal("abc", "abd") is False
