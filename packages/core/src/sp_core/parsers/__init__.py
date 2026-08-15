"""Parser registry.

Dispatch is on **magic bytes first, extension second** — a 10-year archive contains
files whose extension lies, and misrouting a FIT to the GPX parser produces a
confusing error instead of a clean one.
"""

from __future__ import annotations

import gzip
from typing import Protocol

from sp_core.canonical.activity import ParseResult
from sp_core.parsers.csv_index import BulkCsvParser
from sp_core.parsers.fit import FitParser
from sp_core.parsers.gpx import GpxParser
from sp_core.parsers.tcx import TcxParser

__all__ = [
    "BulkCsvParser",
    "FitParser",
    "GpxParser",
    "TcxParser",
    "detect_format",
    "maybe_gunzip",
    "parse_activity_file",
]


class ActivityParser(Protocol):
    source: str

    def parse(self, data: bytes) -> ParseResult: ...


_PARSERS: dict[str, ActivityParser] = {
    "fit": FitParser(),
    "gpx": GpxParser(),
    "tcx": TcxParser(),
}

_GZIP_MAGIC = b"\x1f\x8b"


def maybe_gunzip(data: bytes) -> bytes:
    """Transparently decompress. Most export members are ``.fit.gz`` / ``.tcx.gz``."""
    if data[:2] != _GZIP_MAGIC:
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError):
        return data


def detect_format(data: bytes, filename: str = "") -> str | None:
    """Return ``fit`` / ``gpx`` / ``tcx``, or ``None`` if it is not an activity file."""
    # FIT: bytes 8:12 of the header are the ASCII marker ".FIT".
    if len(data) >= 12 and data[8:12] == b".FIT":
        return "fit"

    head = data[:512].lstrip()
    if head[:1] == b"<":
        lowered = head.lower()
        if b"<gpx" in lowered:
            return "gpx"
        if b"trainingcenterdatabase" in lowered or b"<tcx" in lowered:
            return "tcx"

    name = filename.lower()
    for suffix in (".gz", ".gzip"):
        name = name.removesuffix(suffix)
    for extension in ("fit", "gpx", "tcx"):
        if name.endswith(f".{extension}"):
            return extension
    return None


def parse_activity_file(data: bytes, filename: str = "") -> ParseResult:
    """Decompress, detect, and parse one activity file.

    Raises ``ValueError`` for anything unparseable — the caller records the item as
    failed and carries on with the rest of the import (CLAUDE.md §4.6).
    """
    payload = maybe_gunzip(data)
    detected = detect_format(payload, filename)
    if detected is None:
        raise ValueError(f"unrecognised activity format: {filename or '<unnamed>'}")
    return _PARSERS[detected].parse(payload)
