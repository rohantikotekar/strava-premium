"""TCX parser.

TCX is verbose XML with a fixed schema, so a hand-rolled reader beats adding a
heavyweight dependency. Namespaces vary between Garmin versions, so every lookup
strips the namespace rather than matching a fixed URI.

**Parsed with defusedxml, not stdlib ElementTree.** These files come straight out
of a user-supplied zip, so they are untrusted input: stdlib ElementTree will
happily process a "billion laughs" entity-expansion bomb and take the worker down
with it. defusedxml rejects DTDs and entity expansion outright.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree

import numpy as np
from defusedxml.ElementTree import ParseError as DefusedParseError
from defusedxml.ElementTree import fromstring as safe_fromstring

from sp_core.canonical.activity import CanonicalActivity, Channel, ParseResult, StreamSet
from sp_core.canonical.sports import is_indoor_sport


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _find(parent: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    """Depth-first search for the first descendant matching any local tag name."""
    wanted = {n.lower() for n in names}
    for child in parent.iter():
        if child is not parent and _tag(child).lower() in wanted:
            return child
    return None


def _number(parent: ElementTree.Element | None, *names: str) -> float | None:
    if parent is None:
        return None
    element = _find(parent, *names)
    if element is None or not element.text:
        return None
    try:
        return float(element.text.strip())
    except ValueError:
        return None


def _parse_time(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class TcxParser:
    source = "tcx"

    def parse(self, data: bytes) -> ParseResult:
        try:
            root = safe_fromstring(data)
        except (DefusedParseError, ElementTree.ParseError, ValueError) as exc:
            # defusedxml also raises here for entity-expansion / DTD attacks, which
            # is the same outcome we want: record the item as failed, keep going.
            raise ValueError(f"malformed TCX: {exc}") from exc

        activity_el = next((e for e in root.iter() if _tag(e) == "Activity"), None)
        sport_type = (activity_el.get("Sport") if activity_el is not None else None) or "Workout"

        times: list[float] = []
        rows: dict[Channel, list[float]] = {}
        total_distance = total_time = calories = None
        max_speed = avg_hr = max_hr = None

        for lap in (e for e in root.iter() if _tag(e) == "Lap"):
            total_distance = (total_distance or 0.0) + (_number(lap, "DistanceMeters") or 0.0)
            total_time = (total_time or 0.0) + (_number(lap, "TotalTimeSeconds") or 0.0)
            calories = (calories or 0.0) + (_number(lap, "Calories") or 0.0)
            max_speed = max(max_speed or 0.0, _number(lap, "MaximumSpeed") or 0.0)
            avg_hr = avg_hr or _number(_find(lap, "AverageHeartRateBpm"), "Value")
            max_hr = max_hr or _number(_find(lap, "MaximumHeartRateBpm"), "Value")

            for point in (e for e in lap.iter() if _tag(e) == "Trackpoint"):
                index = len(times)
                time_el = _find(point, "Time")
                times.append(_parse_time(time_el.text if time_el is not None else None) or np.nan)

                sample: dict[Channel, float] = {}
                position = _find(point, "Position")
                if position is not None:
                    lat = _number(position, "LatitudeDegrees")
                    lng = _number(position, "LongitudeDegrees")
                    if lat is not None:
                        sample[Channel.LAT] = lat
                    if lng is not None:
                        sample[Channel.LNG] = lng
                for channel, names in (
                    (Channel.ALTITUDE, ("AltitudeMeters",)),
                    (Channel.DISTANCE, ("DistanceMeters",)),
                    (Channel.CADENCE, ("Cadence", "RunCadence")),
                ):
                    value = _number(point, *names)
                    if value is not None:
                        sample[channel] = value

                hr = _number(_find(point, "HeartRateBpm"), "Value")
                if hr is not None:
                    sample[Channel.HEARTRATE] = hr

                extensions = _find(point, "Extensions")
                if extensions is not None:
                    watts = _number(extensions, "Watts")
                    if watts is not None:
                        sample[Channel.POWER] = watts
                    speed = _number(extensions, "Speed")
                    if speed is not None:
                        sample[Channel.SPEED] = speed

                for channel in sample:
                    if channel not in rows:
                        rows[channel] = [np.nan] * index
                for channel, values in rows.items():
                    values.append(sample.get(channel, np.nan))

        if not times:
            raise ValueError("TCX contains no trackpoints")

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

        lat_stream, lng_stream = streams.get(Channel.LAT), streams.get(Channel.LNG)
        start_lat = start_lng = None
        bbox = None
        if lat_stream is not None and lng_stream is not None:
            valid = np.isfinite(lat_stream) & np.isfinite(lng_stream)
            if bool(valid.any()):
                good_lat, good_lng = lat_stream[valid], lng_stream[valid]
                start_lat, start_lng = float(good_lat[0]), float(good_lng[0])
                bbox = (
                    float(good_lng.min()),
                    float(good_lat.min()),
                    float(good_lng.max()),
                    float(good_lat.max()),
                )

        time_stream = channels[Channel.TIME]
        finite = time_stream[np.isfinite(time_stream)]
        elapsed = int(total_time or (finite.max() if len(finite) else 0))

        activity = CanonicalActivity(
            source="tcx",
            start_time_utc=start_utc,
            start_time_local=start_utc.replace(tzinfo=None),
            elapsed_time_s=elapsed,
            moving_time_s=int(total_time) if total_time else None,
            sport_type=sport_type,
            is_indoor=is_indoor_sport(sport_type) or start_lat is None,
            distance_m=total_distance or None,
            avg_hr_bpm=avg_hr,
            max_hr_bpm=max_hr,
            max_speed_mps=max_speed or None,
            calories=calories or None,
            start_lat=start_lat,
            start_lng=start_lng,
            bbox=bbox,
        )
        return ParseResult(activity=activity, streams=streams)
