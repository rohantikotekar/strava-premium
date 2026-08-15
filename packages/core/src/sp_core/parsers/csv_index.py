"""Parser for ``activities.csv`` — the fast path.

This one file holds ~90 columns per activity and is 80–90% of every dashboard
chart. Parsing it takes seconds where the .fit files take minutes, which is what
lets the dashboard go live almost immediately (INGESTION.md §2).

Defensive by necessity. Strava has changed this file's shape several times and
localises it, so:

* headers are matched by **normalised name**, never by position;
* ``Distance`` and ``Elapsed Time`` appear **twice** with different units — we keep
  every candidate and pick the one that is physically consistent with the others;
* numbers may be locale-formatted (``1.234,5``);
* any column we don't recognise is preserved in ``extra``.

Assumption flagged for M1 verification: the ``Activity Date`` column is treated as
UTC. Older exports appear to use athlete-local time. Verify against a real export
before trusting `start_time_local` from this parser — the deep parse corrects it
from the FIT file's own timestamps where one exists.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sp_core.canonical.activity import CanonicalActivity, ParseResult
from sp_core.canonical.sports import is_indoor_sport

_PAREN = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]")

# Physically plausible average speeds, used to disambiguate duplicate/unit-ambiguous
# distance columns. 0.3 m/s is a slow walk; 30 m/s (108 km/h) is a fast descent.
_MIN_PLAUSIBLE_MPS = 0.3
_MAX_PLAUSIBLE_MPS = 30.0

_DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",
    "%d %b %Y, %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)

# normalised header -> canonical field name
_FIELD_ALIASES: dict[str, str] = {
    "activityid": "strava_activity_id",
    "activitydate": "date",
    "activityname": "name",
    "activitytype": "sport_type",
    "activitydescription": "description",
    "elapsedtime": "elapsed_time_s",
    "movingtime": "moving_time_s",
    "distance": "distance_m",
    "maxspeed": "max_speed_mps",
    "averagespeed": "avg_speed_mps",
    "elevationgain": "elevation_gain_m",
    "elevationloss": "elevation_loss_m",
    "maxheartrate": "max_hr_bpm",
    "averageheartrate": "avg_hr_bpm",
    "averagecadence": "avg_cadence_rpm",
    "averagewatts": "avg_power_w",
    "maxwatts": "max_power_w",
    "weightedaveragepower": "weighted_avg_power_w",
    "calories": "calories",
    "averagetemperature": "avg_temp_c",
    "perceivedexertion": "perceived_exertion",
    "relativeeffort": "relative_effort",
    "commute": "is_commute",
    "activitygear": "gear_external_id",
    "filename": "export_filename",
}


def normalise_header(raw: str) -> str:
    """``"Distance (km)"`` -> ``"distance"``. Strips units, spaces, punctuation."""
    return _NON_ALNUM.sub("", _PAREN.sub("", raw).strip().lower())


def parse_number(raw: str | None) -> float | None:
    """Parse a possibly locale-formatted number. ``None`` for blank/unparseable.

    Handles ``1,234.5`` and ``1.234,5`` by treating whichever separator appears
    last as the decimal point.
    """
    if raw is None:
        return None
    text = raw.strip().replace(" ", "").replace(" ", "")
    if not text or text.lower() in {"na", "n/a", "-", "--", "null", "none"}:
        return None

    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        # A lone comma is a decimal separator only when it looks like one
        # (exactly one, with 1-2 trailing digits). Otherwise it's a thousands mark.
        tail = text.rsplit(",", 1)[1]
        text = text.replace(",", "." if text.count(",") == 1 and len(tail) <= 2 else "")

    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value else None  # reject NaN


def parse_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"true", "1", "yes", "y"}


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pick_distance_m(
    candidates: list[float],
    elapsed_s: int | None,
    moving_s: int | None,
    avg_speed_mps: float | None,
) -> float | None:
    """Choose the metres-valued distance from ambiguous duplicate columns.

    Strava emits ``Distance`` twice — kilometres in the summary block, metres in
    the detail block — and ``csv.DictReader`` would silently keep whichever came
    last. We score each candidate against the activity's own elapsed time and
    average speed and keep the one that is physically coherent.
    """
    values = [c for c in candidates if c is not None and c > 0]
    if not values:
        return 0.0 if candidates else None

    duration = moving_s or elapsed_s
    if avg_speed_mps and duration:
        expected = avg_speed_mps * duration
        return min(values, key=lambda v: abs(v - expected) / max(expected, 1.0))

    if duration:
        plausible = [v for v in values if _MIN_PLAUSIBLE_MPS <= v / duration <= _MAX_PLAUSIBLE_MPS]
        if plausible:
            return max(plausible)

    # No way to discriminate — the larger value is the metres column.
    return max(values)


def _normalise_speed(value: float | None) -> float | None:
    """Older exports use km/h where newer ones use m/s. Clamp the obvious case."""
    if value is None:
        return None
    return value / 3.6 if value > _MAX_PLAUSIBLE_MPS else value


class BulkCsvParser:
    """Parses ``activities.csv`` into canonical activities."""

    source = "bulk_csv"

    def parse(self, data: bytes) -> list[ParseResult]:
        return list(self.iter_parse(data))

    def iter_parse(self, data: bytes) -> Iterator[ParseResult]:
        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))

        try:
            header = next(reader)
        except StopIteration:
            return

        # normalised header -> every column index carrying it (duplicates matter)
        columns: dict[str, list[int]] = {}
        for index, raw in enumerate(header):
            columns.setdefault(normalise_header(raw), []).append(index)

        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            try:
                result = self._parse_row(row, header, columns)
            except (ValueError, TypeError, KeyError):
                # One malformed row never kills the import (CLAUDE.md §4.6).
                continue
            if result is not None:
                yield result

    def _parse_row(
        self, row: list[str], header: list[str], columns: dict[str, list[int]]
    ) -> ParseResult | None:
        def cells(field: str) -> list[str]:
            out = []
            for norm, canonical in _FIELD_ALIASES.items():
                if canonical != field:
                    continue
                for index in columns.get(norm, []):
                    if index < len(row):
                        out.append(row[index])
            return out

        def first(field: str) -> str | None:
            values = [c for c in cells(field) if c.strip()]
            return values[0] if values else None

        def number(field: str) -> float | None:
            for value in cells(field):
                parsed = parse_number(value)
                if parsed is not None:
                    return parsed
            return None

        start = parse_date(first("date"))
        if start is None:
            return None

        elapsed_raw = number("elapsed_time_s")
        moving_raw = number("moving_time_s")
        elapsed_s = int(elapsed_raw) if elapsed_raw is not None else 0
        moving_s = int(moving_raw) if moving_raw is not None else None

        avg_speed = _normalise_speed(number("avg_speed_mps"))
        distance_m = _pick_distance_m(
            [v for v in (parse_number(c) for c in cells("distance_m")) if v is not None],
            elapsed_s,
            moving_s,
            avg_speed,
        )

        sport_type = (first("sport_type") or "Workout").strip()
        activity_id = number("strava_activity_id")
        exertion = number("perceived_exertion")

        # Anything we don't map is kept — today's unknown column is next quarter's
        # feature (CLAUDE.md §4.7).
        extra: dict[str, Any] = {}
        for index, raw_header in enumerate(header):
            if index >= len(row):
                break
            norm = normalise_header(raw_header)
            if norm in _FIELD_ALIASES or not row[index].strip():
                continue
            extra[norm] = row[index].strip()

        export_filename = first("export_filename")
        if export_filename:
            extra["export_filename"] = export_filename

        activity = CanonicalActivity(
            source="bulk_csv",
            strava_activity_id=int(activity_id) if activity_id is not None else None,
            start_time_utc=start,
            # The CSV carries no offset; the deep parse refines these from the FIT.
            start_time_local=start.replace(tzinfo=None),
            utc_offset_s=0,
            elapsed_time_s=elapsed_s,
            moving_time_s=moving_s,
            sport_type=sport_type,
            name=first("name"),
            description=first("description"),
            is_indoor=is_indoor_sport(sport_type),
            is_commute=parse_bool(first("is_commute")),
            gear_external_id=first("gear_external_id"),
            distance_m=distance_m,
            elevation_gain_m=number("elevation_gain_m"),
            elevation_loss_m=number("elevation_loss_m"),
            avg_speed_mps=avg_speed,
            max_speed_mps=_normalise_speed(number("max_speed_mps")),
            avg_hr_bpm=number("avg_hr_bpm"),
            max_hr_bpm=number("max_hr_bpm"),
            avg_cadence_rpm=number("avg_cadence_rpm"),
            avg_power_w=number("avg_power_w"),
            max_power_w=number("max_power_w"),
            weighted_avg_power_w=number("weighted_avg_power_w"),
            calories=number("calories"),
            avg_temp_c=number("avg_temp_c"),
            perceived_exertion=int(exertion) if exertion is not None else None,
            relative_effort=number("relative_effort"),
            extra=extra,
        )
        return ParseResult(activity=activity, streams=None)
