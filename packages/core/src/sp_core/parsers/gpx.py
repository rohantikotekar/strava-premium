"""GPX parser (gpxpy).

GPX carries GPS and time reliably; heart rate, cadence and power live in the
Garmin ``TrackPointExtension`` namespace, which gpxpy exposes as raw XML elements.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import gpxpy
import numpy as np

from sp_core.canonical.activity import CanonicalActivity, Channel, ParseResult, StreamSet
from sp_core.canonical.sports import is_indoor_sport

# Local tag name (namespace stripped) -> channel.
_EXTENSION_FIELDS: dict[str, Channel] = {
    "hr": Channel.HEARTRATE,
    "heartrate": Channel.HEARTRATE,
    "cad": Channel.CADENCE,
    "cadence": Channel.CADENCE,
    "power": Channel.POWER,
    "watts": Channel.POWER,
    "atemp": Channel.TEMPERATURE,
    "temp": Channel.TEMPERATURE,
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _read_extensions(point: gpxpy.gpx.GPXTrackPoint) -> dict[Channel, float]:
    """Walk the TrackPointExtension subtree, which may be nested one or two deep."""
    found: dict[Channel, float] = {}

    def walk(elements: Iterable[Any] | None) -> None:
        for element in elements or []:
            channel = _EXTENSION_FIELDS.get(_local_name(element.tag))
            if channel is not None and element.text:
                with contextlib.suppress(ValueError):
                    found[channel] = float(element.text.strip())
            walk(list(element))

    walk(point.extensions)
    return found


class GpxParser:
    source = "gpx"

    def parse(self, data: bytes) -> ParseResult:
        gpx = gpxpy.parse(data.decode("utf-8", errors="replace"))

        times: list[float] = []
        rows: dict[Channel, list[float]] = {}
        name: str | None = None
        sport_type = "Workout"

        for track in gpx.tracks:
            name = name or track.name
            if track.type:
                sport_type = track.type
            for segment in track.segments:
                for point in segment.points:
                    index = len(times)
                    times.append(point.time.timestamp() if point.time else np.nan)
                    sample: dict[Channel, float] = {}
                    if point.latitude is not None:
                        sample[Channel.LAT] = float(point.latitude)
                    if point.longitude is not None:
                        sample[Channel.LNG] = float(point.longitude)
                    if point.elevation is not None:
                        sample[Channel.ALTITUDE] = float(point.elevation)
                    sample.update(_read_extensions(point))
                    # A channel that first appears mid-track (a strap paired late)
                    # must be back-filled with NaN so every array stays aligned to
                    # the sample index.
                    for channel in sample:
                        if channel not in rows:
                            rows[channel] = [np.nan] * index
                    for channel, values in rows.items():
                        values.append(sample.get(channel, np.nan))

        if not times:
            raise ValueError("GPX contains no track points")

        base = next((t for t in times if t == t), None)
        channels: dict[Channel, np.ndarray] = {
            channel: np.asarray(values, dtype=np.float64) for channel, values in rows.items()
        }
        channels[Channel.TIME] = np.asarray(
            [(t - base) if (t == t and base is not None) else np.nan for t in times],
            dtype=np.float64,
        )

        streams = StreamSet(channels=channels)
        start_utc = datetime.fromtimestamp(base, tz=UTC) if base is not None else datetime.now(UTC)

        moving = gpx.get_moving_data()
        uphill, downhill = gpx.get_uphill_downhill()
        distance = gpx.length_3d() or gpx.length_2d()

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

        time_stream = channels[Channel.TIME]
        finite_time = time_stream[np.isfinite(time_stream)]
        elapsed = int(finite_time.max()) if len(finite_time) else 0

        activity = CanonicalActivity(
            source="gpx",
            start_time_utc=start_utc,
            start_time_local=start_utc.replace(tzinfo=None),
            elapsed_time_s=elapsed,
            moving_time_s=int(moving.moving_time) if moving else None,
            sport_type=sport_type,
            name=name,
            is_indoor=is_indoor_sport(sport_type),
            distance_m=float(distance) if distance else None,
            elevation_gain_m=float(uphill) if uphill else None,
            elevation_loss_m=float(downhill) if downhill else None,
            start_lat=start_lat,
            start_lng=start_lng,
            bbox=bbox,
        )
        return ParseResult(activity=activity, streams=streams)
