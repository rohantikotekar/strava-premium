/**
 * Unit conversion and display formatting.
 *
 * The backend stores SI everywhere (metres, seconds, watts). Conversion to miles,
 * feet and pace happens HERE and nowhere else — unit confusion is the #1 bug
 * source in fitness apps (CLAUDE.md §3).
 */

export type UnitPref = "metric" | "imperial";

const METRES_PER_MILE = 1609.344;
const METRES_PER_FOOT = 0.3048;

export function formatDistance(metres: number | null | undefined, pref: UnitPref): string {
  if (metres === null || metres === undefined) return "—";
  if (metres === 0) return "—"; // a zero-distance activity shows a dash, not "0.0 km"

  if (pref === "imperial") {
    const miles = metres / METRES_PER_MILE;
    return miles < 0.1 ? `${Math.round(metres / METRES_PER_FOOT)} ft` : `${miles.toFixed(1)} mi`;
  }
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function formatElevation(metres: number | null | undefined, pref: UnitPref): string {
  if (metres === null || metres === undefined) return "—";
  return pref === "imperial"
    ? `${Math.round(metres / METRES_PER_FOOT).toLocaleString()} ft`
    : `${Math.round(metres).toLocaleString()} m`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(secs).padStart(2, "0")}s`;
  return `${secs}s`;
}

/** Clock format for splits and PR tables, where columns must align. */
export function formatClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`;
}

/** Runners think in pace; cyclists think in speed. Same underlying m/s. */
export function formatPace(mps: number | null | undefined, pref: UnitPref): string {
  if (!mps || mps <= 0) return "—";
  const secondsPerUnit = pref === "imperial" ? METRES_PER_MILE / mps : 1000 / mps;
  const minutes = Math.floor(secondsPerUnit / 60);
  const seconds = Math.round(secondsPerUnit % 60);
  const normalisedMinutes = seconds === 60 ? minutes + 1 : minutes;
  const normalisedSeconds = seconds === 60 ? 0 : seconds;
  return `${normalisedMinutes}:${String(normalisedSeconds).padStart(2, "0")} /${
    pref === "imperial" ? "mi" : "km"
  }`;
}

export function formatSpeed(mps: number | null | undefined, pref: UnitPref): string {
  if (!mps || mps <= 0) return "—";
  return pref === "imperial"
    ? `${((mps * 3600) / METRES_PER_MILE).toFixed(1)} mph`
    : `${((mps * 3600) / 1000).toFixed(1)} km/h`;
}

/** Pace for run-ish sports, speed for everything with wheels. */
export function formatVelocity(
  mps: number | null | undefined,
  sportGroup: string,
  pref: UnitPref,
): string {
  return sportGroup === "run" || sportGroup === "walk" || sportGroup === "swim"
    ? formatPace(mps, pref)
    : formatSpeed(mps, pref);
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatDelta(pct: number | null | undefined): string | null {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  const rounded = Math.round(pct);
  if (rounded === 0) return "—";
  return `${rounded > 0 ? "↑" : "↓"} ${Math.abs(rounded)}%`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Formats a stat tile by its declared unit. */
export function formatByUnit(
  value: number | null,
  unit: string,
  pref: UnitPref,
): string {
  switch (unit) {
    case "m":
      return formatDistance(value, pref);
    case "s":
      return formatDuration(value);
    case "count":
      return formatNumber(value);
    case "load":
      return formatNumber(value);
    case "W":
      return value === null ? "—" : `${Math.round(value)} W`;
    default:
      return formatNumber(value);
  }
}

export const SPORT_LABELS: Record<string, string> = {
  run: "Run",
  ride: "Ride",
  swim: "Swim",
  walk: "Walk & hike",
  ski: "Ski",
  water: "Water",
  gym: "Gym",
  other: "Other",
};

export function sportLabel(group: string): string {
  return SPORT_LABELS[group] ?? group;
}
