"""A minimal FIT encoder, used to build parser fixtures.

We cannot commit a real .fit file from a real ride — it would contain a home
address (CLAUDE.md §7). fitdecode is read-only, so this writes just enough valid
FIT to exercise the parser: a file_id, a session summary, and N record messages.

Field numbers, base types and scale/offset come from the FIT profile; fitdecode
applies the scaling itself, so the values written here are raw.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime

# FIT timestamps count seconds from 1989-12-31T00:00:00Z, not the Unix epoch.
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)

# Base type ids.
_UINT8 = 0x02
_UINT16 = 0x84
_UINT32 = 0x86
_SINT32 = 0x85
_ENUM = 0x00

_CRC_TABLE = (
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
)

SEMICIRCLES_PER_DEGREE = 2**31 / 180.0


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        checksum = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ checksum ^ _CRC_TABLE[byte & 0xF]

        checksum = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ checksum ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def to_fit_timestamp(moment: datetime) -> int:
    return int((moment - FIT_EPOCH).total_seconds())


def to_semicircles(degrees: float) -> int:
    return int(degrees * SEMICIRCLES_PER_DEGREE)


class FitWriter:
    def __init__(self) -> None:
        self._body = bytearray()

    def define(self, local_type: int, global_num: int, fields: list[tuple[int, int, int]]) -> None:
        """fields: [(field_def_num, size_bytes, base_type)]"""
        self._body.append(0x40 | local_type)  # definition message header
        self._body.append(0)  # reserved
        self._body.append(0)  # architecture: little-endian
        self._body += struct.pack("<H", global_num)
        self._body.append(len(fields))
        for field_num, size, base_type in fields:
            self._body += bytes((field_num, size, base_type))

    def data(self, local_type: int, payload: bytes) -> None:
        self._body.append(local_type)  # data message header
        self._body += payload

    def build(self) -> bytes:
        header = bytearray()
        header.append(14)  # header size
        header.append(0x20)  # protocol version 2.0
        header += struct.pack("<H", 2140)  # profile version
        header += struct.pack("<I", len(self._body))  # data size
        header += b".FIT"
        header += struct.pack("<H", crc16(bytes(header[:12])))

        full = bytes(header) + bytes(self._body)
        return full + struct.pack("<H", crc16(full))


def build_fit(
    *,
    start: datetime,
    n_samples: int = 60,
    sport: int = 1,  # 1 = running in the FIT profile
    with_gps: bool = True,
    with_hr: bool = True,
    with_power: bool = False,
    start_lat: float = 51.5,
    start_lng: float = -0.12,
    speed_mps: float = 3.0,
    hr_bpm: int = 150,
    power_w: int = 200,
) -> bytes:
    """Build a synthetic but structurally valid FIT file."""
    writer = FitWriter()

    # --- file_id (global 0) -------------------------------------------------
    writer.define(0, 0, [(0, 1, _ENUM), (4, 4, _UINT32)])  # type, time_created
    writer.data(0, struct.pack("<BI", 4, to_fit_timestamp(start)))  # 4 = activity

    # --- record (global 20) -------------------------------------------------
    record_fields: list[tuple[int, int, int]] = [(253, 4, _UINT32)]  # timestamp
    if with_gps:
        record_fields += [(0, 4, _SINT32), (1, 4, _SINT32), (2, 2, _UINT16)]  # lat, lng, alt
    record_fields += [(5, 4, _UINT32), (6, 2, _UINT16)]  # distance, speed
    if with_hr:
        record_fields.append((3, 1, _UINT8))
    if with_power:
        record_fields.append((7, 2, _UINT16))

    writer.define(1, 20, record_fields)

    for index in range(n_samples):
        payload = struct.pack("<I", to_fit_timestamp(start) + index)
        if with_gps:
            # Drift roughly north-east so the track has a real bounding box.
            lat = start_lat + index * 0.00002
            lng = start_lng + index * 0.00003
            payload += struct.pack("<i", to_semicircles(lat))
            payload += struct.pack("<i", to_semicircles(lng))
            # altitude is (metres + 500) * 5 in the profile
            payload += struct.pack("<H", int((100 + index * 0.1 + 500) * 5))
        payload += struct.pack("<I", int(index * speed_mps * 100))  # distance, scale 100
        payload += struct.pack("<H", int(speed_mps * 1000))  # speed, scale 1000
        if with_hr:
            payload += struct.pack("<B", hr_bpm)
        if with_power:
            payload += struct.pack("<H", power_w)
        writer.data(1, payload)

    # --- session (global 18) ------------------------------------------------
    writer.define(
        2,
        18,
        [
            (253, 4, _UINT32),  # timestamp
            (2, 4, _UINT32),  # start_time
            (5, 1, _ENUM),  # sport
            (7, 4, _UINT32),  # total_elapsed_time, scale 1000
            (8, 4, _UINT32),  # total_timer_time, scale 1000
            (9, 4, _UINT32),  # total_distance, scale 100
            (14, 2, _UINT16),  # avg_speed, scale 1000
            (16, 1, _UINT8),  # avg_heart_rate
        ],
    )
    total_distance_m = (n_samples - 1) * speed_mps
    writer.data(
        2,
        struct.pack(
            "<IIBIIIHB",
            to_fit_timestamp(start) + n_samples,
            to_fit_timestamp(start),
            sport,
            n_samples * 1000,
            n_samples * 1000,
            int(total_distance_m * 100),
            int(speed_mps * 1000),
            hr_bpm,
        ),
    )

    return writer.build()


def build_truncated_fit(**kwargs: object) -> bytes:
    """A file that dies mid-stream, like a head unit whose battery ran out.

    The parser must keep the records it decoded before the break rather than
    losing the whole ride (CLAUDE.md §4.6).
    """
    full = build_fit(**kwargs)  # type: ignore[arg-type]
    return full[: int(len(full) * 0.6)]
