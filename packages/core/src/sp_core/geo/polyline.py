"""Google encoded polyline.

Routes are stored as an encoded polyline string rather than PostGIS geometry in v1
(ARCHITECTURE.md §3) — it renders directly in any map library and costs one text
column. Simplification keeps a decade of routes small enough to draw at once.
"""

from __future__ import annotations

import numpy as np

_PRECISION = 1e5


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode(points: list[tuple[float, float]]) -> str:
    """Encode ``[(lat, lng), ...]`` into a Google polyline string."""
    output: list[str] = []
    previous_lat = previous_lng = 0
    for lat, lng in points:
        scaled_lat = round(lat * _PRECISION)
        scaled_lng = round(lng * _PRECISION)
        output.append(_encode_value(scaled_lat - previous_lat))
        output.append(_encode_value(scaled_lng - previous_lng))
        previous_lat, previous_lng = scaled_lat, scaled_lng
    return "".join(output)


def decode(encoded: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    index = 0
    lat = lng = 0
    length = len(encoded)

    while index < length:
        for axis in ("lat", "lng"):
            shift = result = 0
            while index < length:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == "lat":
                lat += delta
            else:
                lng += delta
        points.append((lat / _PRECISION, lng / _PRECISION))
    return points


def simplify(points: list[tuple[float, float]], max_points: int = 500) -> list[tuple[float, float]]:
    """Reduce a track to at most ``max_points`` while keeping its shape.

    Stride sampling, not Douglas-Peucker: at 500 points a route is already visually
    indistinguishable at any zoom a dashboard shows, and this is O(n) inside the
    ingest hot path where DP's O(n log n) with recursion is not worth it.
    """
    if len(points) <= max_points:
        return points
    indices = np.linspace(0, len(points) - 1, max_points).round().astype(int)
    return [points[i] for i in dict.fromkeys(indices.tolist())]


def from_streams(lat: np.ndarray, lng: np.ndarray, max_points: int = 500) -> str | None:
    """Build a simplified encoded polyline from lat/lng stream channels."""
    if lat is None or lng is None or len(lat) == 0:
        return None
    valid = np.isfinite(lat) & np.isfinite(lng) & (np.abs(lat) <= 90) & (np.abs(lng) <= 180)
    # An all-zero GPS track is a device fault, not a trip to Null Island.
    valid &= ~((lat == 0) & (lng == 0))
    if not bool(valid.any()):
        return None
    points = list(zip(lat[valid].tolist(), lng[valid].tolist(), strict=True))
    return encode(simplify(points, max_points))
