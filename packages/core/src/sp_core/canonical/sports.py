"""Sport taxonomy.

Strava spells the same sport differently between the bulk export CSV and the API
(``Ride`` / ``ride`` / ``VirtualRide``), and has ~50 values in total. We keep the raw
value on the activity and additionally map to a coarse ``sport_group`` that every
chart and aggregate groups by.

Unknown values map to ``other`` and are never dropped — see CLAUDE.md §4.7.
"""

from __future__ import annotations

SportGroup = str

# raw (normalised: lowercased, spaces/underscores stripped) -> sport_group
_SPORT_GROUPS: dict[str, SportGroup] = {}


def _register(group: SportGroup, *raw_names: str) -> None:
    for name in raw_names:
        _SPORT_GROUPS[_normalise(name)] = group


def _normalise(raw: str) -> str:
    return "".join(ch for ch in raw.lower() if ch.isalnum())


# TCX and older Garmin exports use gerunds ("Running", "Biking") where Strava's
# API and CSV use the bare noun ("Run", "Ride"). Both spellings must map.
_register("run", "Run", "Running", "TrailRun", "VirtualRun", "Treadmill", "Race")
_register(
    "ride",
    "Ride",
    "Biking",
    "MountainBikeRide",
    "GravelRide",
    "VirtualRide",
    "EBikeRide",
    "EMountainBikeRide",
    "Velomobile",
    "Handcycle",
    "Cycling",
)
_register("swim", "Swim", "OpenWaterSwim", "Swimming")
_register("walk", "Walk", "Walking", "Hike", "Hiking", "Snowshoe", "Wheelchair")
_register("ski", "AlpineSki", "BackcountrySki", "NordicSki", "Snowboard", "RollerSki")
_register(
    "water",
    "Kayaking",
    "Canoeing",
    "Rowing",
    "Surfing",
    "StandUpPaddling",
    "Kitesurf",
    "Windsurf",
    "Sail",
)
_register(
    "gym",
    "WeightTraining",
    "Workout",
    "Crossfit",
    "Yoga",
    "Pilates",
    "Elliptical",
    "StairStepper",
    "HighIntensityIntervalTraining",
)

#: Sports recorded indoors by definition — no GPS is expected, so their absence of
#: a latlng stream must not be treated as a data-quality problem.
INDOOR_SPORTS: frozenset[str] = frozenset(
    _normalise(s)
    for s in (
        "VirtualRide",
        "VirtualRun",
        "Treadmill",
        "WeightTraining",
        "Workout",
        "Crossfit",
        "Yoga",
        "Pilates",
        "Elliptical",
        "StairStepper",
        "HighIntensityIntervalTraining",
    )
)

#: Crude per-group intensity multipliers, used only by the last rung of the
#: training-load fallback ladder (duration x factor) when we have neither power nor
#: heart rate. Deliberately conservative — these numbers are labelled "estimated"
#: everywhere they surface. See FEATURES.md § metric formulas.
SPORT_INTENSITY_FACTOR: dict[SportGroup, float] = {
    "run": 1.0,
    "ride": 0.85,
    "swim": 1.0,
    "walk": 0.4,
    "ski": 0.8,
    "water": 0.7,
    "gym": 0.6,
    "other": 0.6,
}


def sport_group(raw_sport: str | None) -> SportGroup:
    """Map a raw Strava sport name to a coarse group. Never raises."""
    if not raw_sport:
        return "other"
    return _SPORT_GROUPS.get(_normalise(raw_sport), "other")


def is_indoor_sport(raw_sport: str | None) -> bool:
    if not raw_sport:
        return False
    return _normalise(raw_sport) in INDOOR_SPORTS


def known_sports() -> frozenset[str]:
    return frozenset(_SPORT_GROUPS)
