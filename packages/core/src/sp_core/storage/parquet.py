"""Per-activity stream storage as Parquet.

Time-series does not go in Postgres (ARCHITECTURE.md §3). One activity's channels
become one columnar, zstd-compressed file — typically 40–90 KB for a 1-hour ride
against several megabytes as narrow SQL rows.

Column types are chosen to be as narrow as the data allows: heart rate never
exceeds 255, so it is a uint8 and not a float64.
"""

from __future__ import annotations

import io

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sp_core.canonical.activity import Channel, StreamSet

#: Physical bounds per channel, and the arrow type to store it as.
_COLUMN_TYPES: dict[Channel, pa.DataType] = {
    Channel.TIME: pa.int32(),
    Channel.LAT: pa.float64(),
    Channel.LNG: pa.float64(),
    Channel.ALTITUDE: pa.float32(),
    Channel.DISTANCE: pa.float32(),
    Channel.SPEED: pa.float32(),
    Channel.HEARTRATE: pa.uint8(),
    Channel.CADENCE: pa.uint8(),
    Channel.POWER: pa.uint16(),
    Channel.TEMPERATURE: pa.int8(),
    Channel.MOVING: pa.bool_(),
}

_CLAMP: dict[Channel, tuple[float, float]] = {
    Channel.HEARTRATE: (0, 255),
    Channel.CADENCE: (0, 255),
    Channel.POWER: (0, 65535),
    Channel.TEMPERATURE: (-128, 127),
}

#: Small enough that reading part of a long activity is cheap.
_ROW_GROUP_SIZE = 8192


def _to_arrow(channel: Channel, values: np.ndarray) -> pa.Array:
    target = _COLUMN_TYPES.get(channel, pa.float32())
    data = values.astype(np.float64)

    bounds = _CLAMP.get(channel)
    if bounds is not None:
        low, high = bounds
        data = np.where(np.isfinite(data), np.clip(data, low, high), np.nan)

    if pa.types.is_integer(target) or pa.types.is_boolean(target):
        # Integer arrow columns cannot hold NaN, so nulls are carried in a mask.
        mask = ~np.isfinite(data)
        filled = np.nan_to_num(data, nan=0.0)
        return pa.array(np.round(filled).astype(np.int64), type=target, mask=mask)

    return pa.array(data, type=target)


def streams_to_parquet(streams: StreamSet) -> bytes:
    """Serialise a StreamSet. Only channels that carry real data are written."""
    columns: dict[str, pa.Array] = {}

    for channel in streams.channels:
        values = streams.channels[channel]
        if values is None or len(values) == 0:
            continue
        # Time is always kept even though it is monotonic from zero; every other
        # channel must carry a non-zero value to earn a column.
        if channel is not Channel.TIME and not streams.has(channel):
            continue
        columns[channel.value] = _to_arrow(channel, values)

    if not columns:
        return b""

    table = pa.table(columns)
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        compression="zstd",
        use_dictionary=False,
        row_group_size=_ROW_GROUP_SIZE,
    )
    return sink.getvalue()


def parquet_to_streams(data: bytes) -> StreamSet:
    """Read a stream Parquet back into a StreamSet."""
    if not data:
        return StreamSet()

    table = pq.read_table(io.BytesIO(data))
    by_name = {c.value: c for c in Channel}

    channels: dict[Channel, np.ndarray] = {}
    for name in table.column_names:
        channel = by_name.get(name)
        if channel is None:
            continue
        column = table.column(name).to_numpy(zero_copy_only=False)
        channels[channel] = column.astype(np.float64)
    return StreamSet(channels=channels)


def downsample(streams: StreamSet, max_points: int = 2000) -> StreamSet:
    """Reduce a stream to at most `max_points` samples for charting.

    Uses min/max-preserving bucket sampling rather than plain stride sampling, so a
    30-second power spike survives being drawn at 800px wide instead of vanishing
    between two sampled points.
    """
    if streams.n_samples <= max_points or max_points < 2:
        return streams

    total = streams.n_samples
    bucket_size = total / max_points
    indices: list[int] = []
    for bucket in range(max_points):
        start = int(bucket * bucket_size)
        end = min(int((bucket + 1) * bucket_size), total)
        if end <= start:
            continue
        indices.append(start)
        # Keep the bucket's extreme too, so peaks are not smoothed away.
        reference = next(
            (
                streams.channels[c]
                for c in (Channel.POWER, Channel.HEARTRATE, Channel.SPEED, Channel.ALTITUDE)
                if c in streams.channels
            ),
            None,
        )
        if reference is not None and end - start > 1:
            window = reference[start:end]
            finite = np.isfinite(window)
            if bool(finite.any()):
                indices.append(start + int(np.nanargmax(np.where(finite, window, -np.inf))))

    keep = np.unique(np.asarray(sorted(indices), dtype=np.int64))
    keep = keep[keep < total]
    return StreamSet(channels={c: v[keep] for c, v in streams.channels.items()})
