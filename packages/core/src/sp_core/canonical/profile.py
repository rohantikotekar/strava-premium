"""The athlete inputs that metric functions need.

Kept as an explicit value object rather than reading from the DB inside metrics —
that is what keeps `sp_core.metrics` pure and testable (CLAUDE.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AthleteProfile:
    ftp_w: int | None = None
    max_hr_bpm: int | None = None
    resting_hr_bpm: int | None = None
    weight_kg: float | None = None
    sex: str | None = None

    @property
    def has_power_zones(self) -> bool:
        return bool(self.ftp_w and self.ftp_w > 0)

    @property
    def has_hr_zones(self) -> bool:
        return bool(
            self.max_hr_bpm
            and self.resting_hr_bpm is not None
            and self.max_hr_bpm > self.resting_hr_bpm
        )
