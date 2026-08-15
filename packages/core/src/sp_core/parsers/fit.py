"""FIT parser (fitdecode).

FIT is the richest format and the one most likely to be malformed — every device
vendor interprets the spec slightly differently, and a truncated file is normal in
a 10-year archive. This parser never raises on bad data: it collects what it can and
lets the caller record a partial or failed item (CLAUDE.md §4.6).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import fitdecode
import numpy as np
import structlog

from sp_core.canonical.activity import CanonicalActivity, Channel, ParseResult, StreamSet
from sp_core.canonical.sports import is_indoor_sport

log = structlog.get_logger(__name__)

# FIT stores lat/lng as "semicircles": a signed 32-bit integer covering +/-180 deg.
_SEMICIRCLE_TO_DEGREES = 180.0 / (2**31)

# record field -> canonical channel
_RECORD_FIELDS: dict[str, Channel] = {
    "timestamp": Channel.TIME,
    "position_lat": Channel.LAT,
    "position_long": Channel.LNG,
    "altitude": Channel.ALTITUDE,
    "enhanced_altitude": Channel.ALTITUDE,
    "distance": Channel.DISTANCE,
    "speed": Channel.SPEED,
    "enhanced_speed": Channel.SPEED,
    "heart_rate": Channel.HEARTRATE,
    "cadence": Channel.CADENCE,
    "power": Channel.POWER,
    "temperature": Channel.TEMPERATURE,
}

# session/lap summary field -> canonical activity attribute
_SESSION_FIELDS: dict[str, str] = {
    "total_distance": "distance_m",
    "total_elapsed_time": "elapsed_time_s",
    "total_timer_time": "moving_time_s",
    "total_ascent": "elevation_gain_m",
    "total_descent": "elevation_loss_m",
    "avg_speed": "avg_speed_mps",
    "enhanced_avg_speed": "avg_speed_mps",
    "max_speed": "max_speed_mps",
    "enhanced_max_speed": "max_speed_mps",
    "avg_heart_rate": "avg_hr_bpm",
    "max_heart_rate": "max_hr_bpm",
    "avg_cadence": "avg_cadence_rpm",
    "avg_power": "avg_power_w",
    "max_power": "max_power_w",
    "normalized_power": "weighted_avg_power_w",
    "total_calories": "calories",
    "avg_temperature": "avg_temp_c",
}


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
        return result if result == result else None
    return None


class FitParser:
    source = "fit"

    def parse(self, data: bytes) -> ParseResult:
        samples: list[dict[Channel, float]] = []
        session: dict[str, float] = {}
        sport_type: str | None = None
        sub_sport: str | None = None
        start: datetime | None = None

        # `check_crc=False`: a truncated file from a device that died mid-ride still
        # has thousands of good records before the break, and losing a whole ride to
        # a CRC mismatch is exactly the failure mode CLAUDE.md §4.6 forbids.
        reader = fitdecode.FitReader(io.BytesIO(data), check_crc=False)
        try:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "record":
                    sample = self._read_record(frame)
                    if sample:
                        samples.append(sample)
                elif frame.name in {"session", "lap"}:
                    self._read_session(frame, session)
                    sport_type = sport_type or self._field(frame, "sport")
                    sub_sport = sub_sport or self._field(frame, "sub_sport")
                    start = start or self._field(frame, "start_time")
                elif frame.name == "sport":
                    sport_type = sport_type or self._field(frame, "sport")
                elif frame.name == "activity":
                    start = start or self._field(frame, "local_timestamp")
        except Exception as exc:
            # Keep whatever was decoded before the corruption. A head unit that died
            # mid-ride still has thousands of good records, and losing the whole
            # activity to a truncated tail is the failure CLAUDE.md §4.6 forbids.
            log.debug("fit.partial_decode", records=len(samples), error=str(exc)[:200])

        if not samples and not session:
            raise ValueError("no decodable FIT records")

        # A truncated file often loses its session frame but keeps thousands of
        # timestamped records, so fall back to the first record's clock.
        if start is None:
            first_epoch = next((s[Channel.TIME] for s in samples if Channel.TIME in s), None)
            if first_epoch is not None:
                start = datetime.fromtimestamp(first_epoch, tz=UTC)

        streams = self._build_streams(samples)
        activity = self._build_activity(streams, session, sport_type, sub_sport, start)
        return ParseResult(activity=activity, streams=streams)

    def _field(self, frame: Any, name: str) -> Any:
        try:
            return frame.get_value(name) if frame.has_field(name) else None
        except (KeyError, ValueError):
            return None

    def _read_record(self, frame: Any) -> dict[Channel, float]:
        sample: dict[Channel, float] = {}
        for field_name, channel in _RECORD_FIELDS.items():
            raw = self._field(frame, field_name)
            if raw is None:
                continue
            if channel is Channel.TIME:
                if isinstance(raw, datetime):
                    sample[channel] = raw.timestamp()
                continue
            value = _as_float(raw)
            if value is None:
                continue
            if channel in (Channel.LAT, Channel.LNG):
                value *= _SEMICIRCLE_TO_DEGREES
                if not -180.0 <= value <= 180.0:
                    continue
            sample[channel] = value
        return sample

    def _read_session(self, frame: Any, into: dict[str, float]) -> None:
        for field_name, attr in _SESSION_FIELDS.items():
            value = _as_float(self._field(frame, field_name))
            if value is None:
                continue
            # First writer wins: `session` frames precede our use and are more
            # authoritative than per-lap values, which would otherwise overwrite
            # the total with the last lap's number.
            into.setdefault(attr, value)

    def _build_streams(self, samples: list[dict[Channel, float]]) -> StreamSet:
        if not samples:
            return StreamSet()

        present = {channel for sample in samples for channel in sample}
        channels: dict[Channel, np.ndarray] = {}

        epochs = [s.get(Channel.TIME) for s in samples]
        base = next((e for e in epochs if e is not None), None)

        for channel in present:
            if channel is Channel.TIME:
                continue
            values = [sample.get(channel, np.nan) for sample in samples]
            channels[channel] = np.asarray(values, dtype=np.float64)

        if base is not None:
            channels[Channel.TIME] = np.asarray(
                [(e - base) if e is not None else np.nan for e in epochs], dtype=np.float64
            )
        else:
            channels[Channel.TIME] = np.arange(len(samples), dtype=np.float64)

        return StreamSet(channels=channels)

    def _build_activity(
        self,
        streams: StreamSet,
        session: dict[str, float],
        sport_type: str | None,
        sub_sport: str | None,
        start: datetime | None,
    ) -> CanonicalActivity:
        if not isinstance(start, datetime):
            # Never invent a date. Parsers take no clock reads (CLAUDE.md §3), and
            # a fabricated timestamp would silently land the activity on today in
            # the training calendar. Failing lets the CSV row supply the real date.
            raise ValueError("FIT file has no usable start time")

        start_utc = start
        if start_utc.tzinfo is None:
            start_utc = start_utc.replace(tzinfo=UTC)
        start_utc = start_utc.astimezone(UTC)

        elapsed = session.get("elapsed_time_s")
        if elapsed is None:
            time_stream = streams.get(Channel.TIME)
            elapsed = (
                float(np.nanmax(time_stream))
                if time_stream is not None and len(time_stream)
                else 0.0
            )

        sport = (sub_sport or sport_type or "Workout").replace("_", " ").title().replace(" ", "")

        lat = streams.get(Channel.LAT)
        lng = streams.get(Channel.LNG)
        start_lat = start_lng = None
        bbox = None
        if lat is not None and lng is not None:
            valid = np.isfinite(lat) & np.isfinite(lng)
            if bool(valid.any()):
                good_lat, good_lng = lat[valid], lng[valid]
                start_lat, start_lng = float(good_lat[0]), float(good_lng[0])
                bbox = (
                    float(good_lng.min()),
                    float(good_lat.min()),
                    float(good_lng.max()),
                    float(good_lat.max()),
                )

        return CanonicalActivity(
            source="fit",
            start_time_utc=start_utc,
            start_time_local=start_utc.replace(tzinfo=None),
            elapsed_time_s=int(elapsed),
            moving_time_s=int(session["moving_time_s"]) if "moving_time_s" in session else None,
            sport_type=sport,
            is_indoor=is_indoor_sport(sport) or start_lat is None,
            distance_m=session.get("distance_m"),
            elevation_gain_m=session.get("elevation_gain_m"),
            elevation_loss_m=session.get("elevation_loss_m"),
            avg_speed_mps=session.get("avg_speed_mps"),
            max_speed_mps=session.get("max_speed_mps"),
            avg_hr_bpm=session.get("avg_hr_bpm"),
            max_hr_bpm=session.get("max_hr_bpm"),
            avg_cadence_rpm=session.get("avg_cadence_rpm"),
            avg_power_w=session.get("avg_power_w"),
            max_power_w=session.get("max_power_w"),
            weighted_avg_power_w=session.get("weighted_avg_power_w"),
            calories=session.get("calories"),
            avg_temp_c=session.get("avg_temp_c"),
            start_lat=start_lat,
            start_lng=start_lng,
            bbox=bbox,
        )
