"""Builds a synthetic Strava bulk export archive.

Mirrors the real export's shape closely enough to exercise the whole pipeline:
`activities.csv` with the duplicate-Distance quirk, a mix of .fit/.gpx/.tcx
members (some gzipped), media that must be skipped, and a deliberately corrupt
file so partial-failure handling is exercised on every run.

We cannot commit a real export — it would contain a home address (CLAUDE.md §7).
"""

from __future__ import annotations

import gzip
import io
import zipfile
from datetime import UTC, datetime, timedelta

from tests.fixtures.fit_builder import build_fit, build_truncated_fit

_CSV_HEADER = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Activity Description,"
    "Elapsed Time,Distance,Filename,Moving Time,Distance,Average Speed,Max Speed,"
    "Elevation Gain,Average Heart Rate,Max Heart Rate,Average Watts,Max Watts,"
    "Calories,Activity Gear,Commute,Perceived Exertion"
)

_GPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><name>{name}</name><type>{sport}</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""

_TCX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities><Activity Sport="{sport}">
    <Id>{start}</Id>
    <Lap StartTime="{start}">
      <TotalTimeSeconds>{seconds}</TotalTimeSeconds>
      <DistanceMeters>{distance}</DistanceMeters>
      <Calories>200</Calories>
      <AverageHeartRateBpm><Value>145</Value></AverageHeartRateBpm>
      <Track>
{points}
      </Track>
    </Lap>
  </Activity></Activities>
</TrainingCenterDatabase>
"""


def _gpx(start: datetime, name: str, sport: str, n: int = 120) -> bytes:
    points = []
    for index in range(n):
        moment = (start + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        points.append(
            f'    <trkpt lat="{51.5 + index * 2e-5:.6f}" lon="{-0.12 + index * 3e-5:.6f}">'
            f"<ele>{100 + index * 0.1:.1f}</ele><time>{moment}</time>"
            f"<extensions><gpxtpx:TrackPointExtension>"
            f"<gpxtpx:hr>{140 + index % 20}</gpxtpx:hr>"
            f"<gpxtpx:cad>{80 + index % 10}</gpxtpx:cad>"
            f"</gpxtpx:TrackPointExtension></extensions></trkpt>"
        )
    return _GPX_TEMPLATE.format(name=name, sport=sport, points="\n".join(points)).encode()


def _tcx(start: datetime, sport: str, n: int = 90) -> bytes:
    points = []
    for index in range(n):
        moment = (start + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        points.append(
            f"        <Trackpoint><Time>{moment}</Time>"
            f"<Position><LatitudeDegrees>{51.5 + index * 2e-5:.6f}</LatitudeDegrees>"
            f"<LongitudeDegrees>{-0.12 + index * 3e-5:.6f}</LongitudeDegrees></Position>"
            f"<AltitudeMeters>{100 + index * 0.1:.1f}</AltitudeMeters>"
            f"<DistanceMeters>{index * 3.0:.1f}</DistanceMeters>"
            f"<HeartRateBpm><Value>{140 + index % 15}</Value></HeartRateBpm>"
            f"</Trackpoint>"
        )
    return _TCX_TEMPLATE.format(
        sport=sport,
        start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        seconds=n,
        distance=n * 3.0,
        points="\n".join(points),
    ).encode()


def build_export_zip(*, n_activities: int = 12, start: datetime | None = None) -> bytes:
    """Return the bytes of a synthetic export archive.

    Deliberately heterogeneous — a run with HR, a ride with power, an indoor
    session with no GPS, a yoga class with no distance, and one corrupt file.
    """
    origin = start or datetime(2026, 1, 6, 7, 0, tzinfo=UTC)
    buffer = io.BytesIO()
    rows: list[str] = []

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(n_activities):
            activity_id = 1_000_000 + index
            begins = origin + timedelta(days=index * 2, hours=index % 5)
            # Strava's format is "Mar 14, 2026, 7:30:00 AM" — no zero padding on
            # day or hour. Windows strftime has no %-d/%-I, so build it by hand.
            date_text = (
                f"{begins.strftime('%b')} {begins.day}, {begins.year}, "
                f"{(begins.hour % 12) or 12}:{begins.minute:02d}:{begins.second:02d} "
                f"{'AM' if begins.hour < 12 else 'PM'}"
            )

            kind = index % 6
            if kind == 0:  # outdoor run with HR, .fit
                sport, member, payload = (
                    "Run",
                    f"activities/{activity_id}.fit",
                    build_fit(start=begins, n_samples=600, with_hr=True, speed_mps=3.2),
                )
                distance, hr, watts, gear = 1920.0, 152, "", "Daily Trainers"
            elif kind == 1:  # ride with power, gzipped .fit
                sport, member = "Ride", f"activities/{activity_id}.fit.gz"
                payload = gzip.compress(
                    build_fit(
                        start=begins,
                        n_samples=1800,
                        with_hr=True,
                        with_power=True,
                        speed_mps=8.0,
                        sport=2,
                    )
                )
                distance, hr, watts, gear = 14400.0, 141, "205", "Road Bike"
            elif kind == 2:  # run recorded by phone, .gpx
                sport, member = "Run", f"activities/{activity_id}.gpx"
                payload = _gpx(begins, "Lunch Run", "Run")
                distance, hr, watts, gear = 360.0, 148, "", "Daily Trainers"
            elif kind == 3:  # .tcx from an older device
                sport, member = "Running", f"activities/{activity_id}.tcx.gz"
                payload = gzip.compress(_tcx(begins, "Running"))
                distance, hr, watts, gear = 270.0, 145, "", ""
            elif kind == 4:  # indoor trainer, no GPS
                sport, member = "VirtualRide", f"activities/{activity_id}.fit"
                payload = build_fit(
                    start=begins,
                    n_samples=1200,
                    with_gps=False,
                    with_hr=True,
                    with_power=True,
                    speed_mps=7.5,
                    sport=2,
                )
                distance, hr, watts, gear = 9000.0, 138, "190", "Road Bike"
            else:  # strength session: no distance, no file
                sport, member, payload = "WeightTraining", "", b""
                distance, hr, watts, gear = 0.0, 0, "", ""

            if member:
                archive.writestr(member, payload)

            elapsed = 1800 + index * 60
            speed = round(distance / elapsed, 3) if distance else 0
            rows.append(
                f'{activity_id},"{date_text}","Session {index + 1}",{sport},,'
                f"{elapsed},{distance / 1000:.2f},{member},{elapsed},{distance:.1f},"
                f"{speed},{speed * 1.3:.3f},{50 + index * 3},"
                f"{hr or ''},{(hr + 15) if hr else ''},{watts},"
                f"{(int(watts) + 80) if watts else ''},{300 + index * 10},{gear},"
                f"{'true' if index % 7 == 0 else 'false'},{(index % 10) + 1}"
            )

        # A file the parser must record as failed without killing the import.
        archive.writestr("activities/9999999.fit", b"this is not a valid FIT file at all")
        # Truncated file: keeps whatever decoded before the break.
        archive.writestr("activities/9999998.fit", build_truncated_fit(start=origin, n_samples=300))
        # Media must be skipped without being downloaded.
        archive.writestr("media/photo1.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 2048)

        archive.writestr("activities.csv", (_CSV_HEADER + "\n" + "\n".join(rows)).encode())
        archive.writestr("profile.csv", b"First Name,Last Name\nTest,Athlete\n")

    return buffer.getvalue()
