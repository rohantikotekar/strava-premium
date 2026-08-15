"""Parser golden tests.

Covers the cases CLAUDE.md §6 makes mandatory: run, ride, no-GPS, no-HR, indoor,
corrupt/truncated file, zero distance, duplicate/locale-formatted CSV columns.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime

import pytest
from sp_core.canonical.activity import Channel
from sp_core.canonical.sports import is_indoor_sport, sport_group
from sp_core.parsers import detect_format, maybe_gunzip, parse_activity_file
from sp_core.parsers.csv_index import (
    BulkCsvParser,
    normalise_header,
    parse_date,
    parse_number,
)

from tests.fixtures.fit_builder import build_fit, build_truncated_fit

START = datetime(2026, 3, 14, 7, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# activities.csv — the fast path
# --------------------------------------------------------------------------- #


class TestHeaderNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Activity ID", "activityid"),
            ("Distance (km)", "distance"),
            ("Elevation Gain", "elevationgain"),
            ("  Average Heart Rate  ", "averageheartrate"),
            ("Max Speed (m/s)", "maxspeed"),
        ],
    )
    def test_strips_units_and_punctuation(self, raw, expected):
        assert normalise_header(raw) == expected


class TestNumberParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1234.5", 1234.5),
            ("1,234.5", 1234.5),  # en-US thousands
            ("1.234,5", 1234.5),  # de-DE
            ("1234,5", 1234.5),  # lone decimal comma
            ("1,234", 1234.0),  # lone thousands comma
            ("", None),
            ("  ", None),
            ("N/A", None),
            ("not a number", None),
        ],
    )
    def test_locale_formats(self, raw, expected):
        result = parse_number(raw)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)


class TestDateParsing:
    @pytest.mark.parametrize(
        "raw",
        [
            "Mar 14, 2026, 7:30:00 AM",
            "2026-03-14 07:30:00",
            "2026-03-14T07:30:00Z",
        ],
    )
    def test_known_formats(self, raw):
        parsed = parse_date(raw)
        assert parsed is not None
        assert parsed.year == 2026 and parsed.month == 3 and parsed.day == 14

    def test_unparseable_returns_none(self):
        assert parse_date("last Tuesday") is None
        assert parse_date("") is None


def _csv(rows: str, header: str) -> bytes:
    return (header + "\n" + rows).encode("utf-8")


class TestBulkCsvParser:
    def test_parses_a_basic_row(self):
        data = _csv(
            '12345,"Mar 14, 2026, 7:30:00 AM","Morning Run",Run,,3600,10000',
            "Activity ID,Activity Date,Activity Name,Activity Type,"
            "Activity Description,Elapsed Time,Distance",
        )
        results = BulkCsvParser().parse(data)
        assert len(results) == 1

        activity = results[0].activity
        assert activity.strava_activity_id == 12345
        assert activity.name == "Morning Run"
        assert activity.sport_type == "Run"
        assert activity.sport_group == "run"
        assert activity.elapsed_time_s == 3600
        assert activity.distance_m == pytest.approx(10000)
        assert activity.source == "bulk_csv"

    def test_disambiguates_duplicate_distance_columns(self):
        """Strava emits `Distance` twice — km in the summary block, metres in the
        detail block. DictReader would silently keep the last one; we must pick the
        one consistent with elapsed time and average speed.

        3600 s at 2.78 m/s is 10,000 m. The '10' column is kilometres.
        """
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Run,3600,10,2.78,10000',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Distance,Average Speed,Distance",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.distance_m == pytest.approx(10008.0, rel=0.01)

    def test_converts_kmh_speed_to_mps(self):
        """Older exports use km/h. 36 km/h = 10 m/s."""
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Ride,3600,36000,36',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Distance,Average Speed",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.avg_speed_mps == pytest.approx(10.0)

    def test_preserves_unknown_columns_in_extra(self):
        """Today's unknown column is next quarter's feature (CLAUDE.md §4.7)."""
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Run,3600,SomeNewMetric',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Dog Walked With",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.extra["dogwalkedwith"] == "SomeNewMetric"

    def test_captures_the_filename_link(self):
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Run,3600,activities/1.fit.gz',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Filename",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.extra["export_filename"] == "activities/1.fit.gz"

    def test_missing_measures_are_none_never_zero(self):
        """A blank heart-rate column must not become 0 bpm — that corrupts every
        average downstream (CLAUDE.md §4)."""
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Run,3600,,',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Average Heart Rate,Average Watts",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.avg_hr_bpm is None
        assert activity.avg_power_w is None

    def test_zero_distance_activity_is_kept(self):
        """Yoga and strength sessions are real activities with no distance."""
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Yoga,3600,0',
            "Activity ID,Activity Date,Activity Type,Elapsed Time,Distance",
        )
        activity = BulkCsvParser().parse(data)[0].activity
        assert activity.distance_m == 0.0
        assert activity.sport_group == "gym"

    def test_skips_a_malformed_row_without_losing_the_rest(self):
        header = "Activity ID,Activity Date,Activity Type,Elapsed Time"
        data = _csv(
            '1,"Mar 14, 2026, 7:30:00 AM",Run,3600\n'
            "2,not-a-date,Run,3600\n"
            '3,"Mar 15, 2026, 7:30:00 AM",Run,1800',
            header,
        )
        results = BulkCsvParser().parse(data)
        assert len(results) == 2
        assert [r.activity.strava_activity_id for r in results] == [1, 3]

    def test_empty_file(self):
        assert BulkCsvParser().parse(b"") == []

    def test_header_only(self):
        assert BulkCsvParser().parse(b"Activity ID,Activity Date\n") == []


# --------------------------------------------------------------------------- #
# Format detection
# --------------------------------------------------------------------------- #


class TestFormatDetection:
    def test_detects_fit_by_magic_bytes(self):
        data = build_fit(start=START, n_samples=5)
        assert detect_format(data, "whatever.bin") == "fit"

    def test_detects_gpx_by_content(self):
        data = b'<?xml version="1.0"?><gpx version="1.1"></gpx>'
        assert detect_format(data, "mystery") == "gpx"

    def test_detects_tcx_by_content(self):
        data = b'<?xml version="1.0"?><TrainingCenterDatabase></TrainingCenterDatabase>'
        assert detect_format(data, "mystery") == "tcx"

    def test_falls_back_to_extension_through_gz(self):
        assert detect_format(b"junk-bytes", "activities/123.gpx.gz") == "gpx"

    def test_returns_none_for_unrelated_files(self):
        assert detect_format(b"just some text", "notes.txt") is None

    def test_gunzip_is_transparent(self):
        original = b"<gpx></gpx>"
        assert maybe_gunzip(gzip.compress(original)) == original

    def test_gunzip_passes_through_plain_bytes(self):
        assert maybe_gunzip(b"<gpx></gpx>") == b"<gpx></gpx>"


# --------------------------------------------------------------------------- #
# FIT
# --------------------------------------------------------------------------- #


class TestFitParser:
    def test_outdoor_run_with_gps_and_hr(self):
        data = build_fit(start=START, n_samples=60, with_gps=True, with_hr=True)
        result = parse_activity_file(data, "activities/1.fit")

        activity, streams = result.activity, result.streams
        assert activity.source == "fit"
        assert streams is not None
        assert streams.n_samples == 60
        assert streams.has(Channel.HEARTRATE)
        assert streams.has(Channel.LAT)
        assert activity.start_lat == pytest.approx(51.5, abs=1e-4)
        assert activity.bbox is not None

    def test_power_channel_present_only_when_recorded(self):
        without = parse_activity_file(build_fit(start=START, with_power=False), "a.fit")
        with_power = parse_activity_file(build_fit(start=START, with_power=True), "b.fit")

        assert without.streams is not None and not without.streams.has(Channel.POWER)
        assert with_power.streams is not None and with_power.streams.has(Channel.POWER)

    def test_indoor_activity_has_no_gps(self):
        """No lat/lng must not be treated as a data-quality problem for a trainer."""
        data = build_fit(start=START, n_samples=30, with_gps=False)
        result = parse_activity_file(data, "activities/2.fit")

        assert result.streams is not None
        assert not result.streams.has(Channel.LAT)
        assert result.activity.start_lat is None
        assert result.activity.is_indoor is True

    def test_no_heart_rate_leaves_the_channel_absent(self):
        data = build_fit(start=START, with_hr=False)
        result = parse_activity_file(data, "a.fit")
        assert result.streams is not None
        assert not result.streams.has(Channel.HEARTRATE)

    def test_truncated_file_keeps_what_was_decoded(self):
        """A head unit that died mid-ride must not cost the whole activity."""
        data = build_truncated_fit(start=START, n_samples=200)
        result = parse_activity_file(data, "activities/3.fit")
        assert result.streams is not None
        assert result.streams.n_samples > 0

    def test_gzipped_fit(self):
        data = gzip.compress(build_fit(start=START, n_samples=10))
        result = parse_activity_file(data, "activities/4.fit.gz")
        assert result.streams is not None
        assert result.streams.n_samples == 10

    def test_garbage_raises_value_error(self):
        """Unparseable input must raise cleanly so the item is recorded as failed."""
        with pytest.raises(ValueError):
            parse_activity_file(b"not a fit file at all", "broken.fit")

    def test_timestamps_are_utc_aware(self):
        result = parse_activity_file(build_fit(start=START, n_samples=5), "a.fit")
        assert result.activity.start_time_utc.tzinfo is not None
        assert result.activity.start_time_local.tzinfo is None

    def test_start_time_comes_from_the_file_never_from_the_clock(self):
        """Regression: the parser used to fall back to datetime.now() when a
        truncated file lost its session frame. That silently dated the activity
        today and put a phantom entry on the training calendar — and parsers are
        not allowed to read the clock at all (CLAUDE.md §3).
        """
        result = parse_activity_file(build_truncated_fit(start=START, n_samples=400), "a.fit")
        # Must match the file's own timestamps, not the moment the test ran.
        assert result.activity.start_time_utc.date() == START.date()
        assert abs((result.activity.start_time_utc - START).total_seconds()) < 60

    def test_intact_file_start_matches_the_session_frame(self):
        result = parse_activity_file(build_fit(start=START, n_samples=30), "a.fit")
        assert abs((result.activity.start_time_utc - START).total_seconds()) < 2


# --------------------------------------------------------------------------- #
# GPX
# --------------------------------------------------------------------------- #

GPX_WITH_EXTENSIONS = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk>
    <name>Evening Ride</name>
    <type>Ride</type>
    <trkseg>
      <trkpt lat="51.5000" lon="-0.1200">
        <ele>100.0</ele><time>2026-03-14T07:30:00Z</time>
        <extensions><gpxtpx:TrackPointExtension>
          <gpxtpx:hr>140</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>
        </gpxtpx:TrackPointExtension></extensions>
      </trkpt>
      <trkpt lat="51.5010" lon="-0.1210">
        <ele>105.0</ele><time>2026-03-14T07:31:00Z</time>
        <extensions><gpxtpx:TrackPointExtension>
          <gpxtpx:hr>150</gpxtpx:hr><gpxtpx:cad>90</gpxtpx:cad>
        </gpxtpx:TrackPointExtension></extensions>
      </trkpt>
      <trkpt lat="51.5020" lon="-0.1220">
        <ele>110.0</ele><time>2026-03-14T07:32:00Z</time>
      </trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class TestGpxParser:
    def test_reads_track_and_garmin_extensions(self):
        result = parse_activity_file(GPX_WITH_EXTENSIONS.encode(), "activities/5.gpx")
        activity, streams = result.activity, result.streams

        assert activity.source == "gpx"
        assert activity.name == "Evening Ride"
        assert activity.sport_group == "ride"
        assert streams is not None
        assert streams.n_samples == 3
        assert streams.has(Channel.HEARTRATE)
        assert streams.has(Channel.ALTITUDE)

    def test_backfills_channels_that_appear_late(self):
        """A strap paired mid-ride must not shorten the array and desync the rest."""
        gpx = GPX_WITH_EXTENSIONS.replace(
            "<extensions><gpxtpx:TrackPointExtension>\n"
            "          <gpxtpx:hr>140</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>\n"
            "        </gpxtpx:TrackPointExtension></extensions>",
            "",
            1,
        )
        result = parse_activity_file(gpx.encode(), "a.gpx")
        assert result.streams is not None
        for values in result.streams.channels.values():
            assert len(values) == result.streams.n_samples

    def test_computes_distance_and_elevation(self):
        result = parse_activity_file(GPX_WITH_EXTENSIONS.encode(), "a.gpx")
        assert result.activity.distance_m is not None
        assert result.activity.distance_m > 0
        assert result.activity.elevation_gain_m == pytest.approx(10.0, abs=0.5)

    def test_empty_track_raises(self):
        empty = b'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg/></trk></gpx>'
        with pytest.raises(ValueError):
            parse_activity_file(empty, "empty.gpx")


# --------------------------------------------------------------------------- #
# TCX
# --------------------------------------------------------------------------- #

TCX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-03-14T07:30:00Z</Id>
      <Lap StartTime="2026-03-14T07:30:00Z">
        <TotalTimeSeconds>120.0</TotalTimeSeconds>
        <DistanceMeters>400.0</DistanceMeters>
        <MaximumSpeed>4.2</MaximumSpeed>
        <Calories>30</Calories>
        <AverageHeartRateBpm><Value>145</Value></AverageHeartRateBpm>
        <MaximumHeartRateBpm><Value>160</Value></MaximumHeartRateBpm>
        <Track>
          <Trackpoint>
            <Time>2026-03-14T07:30:00Z</Time>
            <Position><LatitudeDegrees>51.5</LatitudeDegrees>
                      <LongitudeDegrees>-0.12</LongitudeDegrees></Position>
            <AltitudeMeters>100</AltitudeMeters>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-03-14T07:31:00Z</Time>
            <Position><LatitudeDegrees>51.501</LatitudeDegrees>
                      <LongitudeDegrees>-0.121</LongitudeDegrees></Position>
            <AltitudeMeters>105</AltitudeMeters>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>150</Value></HeartRateBpm>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


class TestTcxParser:
    def test_reads_laps_and_trackpoints(self):
        result = parse_activity_file(TCX_SAMPLE.encode(), "activities/6.tcx")
        activity, streams = result.activity, result.streams

        assert activity.source == "tcx"
        assert activity.sport_type == "Running"
        assert activity.sport_group == "run"
        assert activity.distance_m == pytest.approx(400.0)
        assert activity.avg_hr_bpm == pytest.approx(145.0)
        assert streams is not None
        assert streams.n_samples == 2
        assert streams.has(Channel.HEARTRATE)

    def test_malformed_xml_raises(self):
        with pytest.raises(ValueError):
            parse_activity_file(b"<TrainingCenterDatabase><unclosed>", "broken.tcx")

    def test_no_trackpoints_raises(self):
        minimal = (
            b'<?xml version="1.0"?><TrainingCenterDatabase>'
            b'<Activities><Activity Sport="Running"></Activity></Activities>'
            b"</TrainingCenterDatabase>"
        )
        with pytest.raises(ValueError):
            parse_activity_file(minimal, "empty.tcx")


# --------------------------------------------------------------------------- #
# Sport taxonomy
# --------------------------------------------------------------------------- #


class TestSportTaxonomy:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Run", "run"),
            ("TrailRun", "run"),
            ("VirtualRun", "run"),
            ("Ride", "ride"),
            ("VirtualRide", "ride"),
            ("GravelRide", "ride"),
            ("Swim", "swim"),
            ("Hike", "walk"),
            ("AlpineSki", "ski"),
            ("Kayaking", "water"),
            ("WeightTraining", "gym"),
            ("Yoga", "gym"),
        ],
    )
    def test_known_sports(self, raw, expected):
        assert sport_group(raw) == expected

    def test_case_and_punctuation_insensitive(self):
        assert sport_group("virtual_ride") == "ride"
        assert sport_group("VIRTUALRIDE") == "ride"
        assert sport_group("Virtual Ride") == "ride"

    def test_unknown_sport_falls_through_to_other_never_dropped(self):
        assert sport_group("Quidditch") == "other"
        assert sport_group(None) == "other"
        assert sport_group("") == "other"

    def test_indoor_detection(self):
        assert is_indoor_sport("VirtualRide") is True
        assert is_indoor_sport("Yoga") is True
        assert is_indoor_sport("Run") is False
