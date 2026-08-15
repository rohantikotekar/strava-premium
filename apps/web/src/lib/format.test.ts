import { describe, expect, it } from "vitest";
import {
  formatClock,
  formatDelta,
  formatDistance,
  formatDuration,
  formatPace,
  formatSpeed,
  formatVelocity,
  sportLabel,
} from "./format";

/**
 * Unit conversion is the #1 bug source in fitness apps (CLAUDE.md §3): the API
 * speaks SI everywhere and conversion happens only here.
 */
describe("formatDistance", () => {
  it("converts metres to km", () => {
    expect(formatDistance(10000, "metric")).toBe("10.0 km");
    expect(formatDistance(1500, "metric")).toBe("1.5 km");
  });

  it("stays in metres below 1 km", () => {
    expect(formatDistance(400, "metric")).toBe("400 m");
  });

  it("converts metres to miles", () => {
    // 1 mile = 1609.344 m
    expect(formatDistance(1609.344, "imperial")).toBe("1.0 mi");
    expect(formatDistance(5000, "imperial")).toBe("3.1 mi");
  });

  it("shows a dash for a zero-distance activity, never '0.0 km'", () => {
    // Yoga and strength sessions are real activities with no distance.
    expect(formatDistance(0, "metric")).toBe("—");
  });

  it("shows a dash for missing data", () => {
    expect(formatDistance(null, "metric")).toBe("—");
    expect(formatDistance(undefined, "metric")).toBe("—");
  });
});

describe("formatDuration", () => {
  it("formats hours and minutes", () => {
    expect(formatDuration(3720)).toBe("1h 02m");
  });

  it("formats minutes and seconds", () => {
    expect(formatDuration(125)).toBe("2m 05s");
  });

  it("formats bare seconds", () => {
    expect(formatDuration(45)).toBe("45s");
  });

  it("handles missing values", () => {
    expect(formatDuration(null)).toBe("—");
  });
});

describe("formatClock", () => {
  it("formats mm:ss under an hour", () => {
    expect(formatClock(1245)).toBe("20:45");
  });

  it("formats h:mm:ss over an hour", () => {
    expect(formatClock(3725)).toBe("1:02:05");
  });
});

describe("formatPace", () => {
  it("converts m/s to min/km", () => {
    // 1000 m / 4 m/s = 250 s = 4:10 per km
    expect(formatPace(4, "metric")).toBe("4:10 /km");
  });

  it("converts m/s to min/mi", () => {
    // 1609.344 / 4 = 402.3 s = 6:42 per mile
    expect(formatPace(4, "imperial")).toBe("6:42 /mi");
  });

  it("rolls 60 seconds up to the next minute", () => {
    const result = formatPace(1000 / 299.6, "metric");
    expect(result).not.toContain(":60");
  });

  it("returns a dash for zero or missing speed", () => {
    expect(formatPace(0, "metric")).toBe("—");
    expect(formatPace(null, "metric")).toBe("—");
  });
});

describe("formatSpeed", () => {
  it("converts m/s to km/h", () => {
    expect(formatSpeed(10, "metric")).toBe("36.0 km/h");
  });

  it("converts m/s to mph", () => {
    expect(formatSpeed(10, "imperial")).toBe("22.4 mph");
  });
});

describe("formatVelocity", () => {
  it("uses pace for runs and speed for rides", () => {
    // Runners think in pace; cyclists think in speed. Same underlying m/s.
    expect(formatVelocity(4, "run", "metric")).toContain("/km");
    expect(formatVelocity(10, "ride", "metric")).toContain("km/h");
  });

  it("uses pace for swims and walks", () => {
    expect(formatVelocity(1.5, "swim", "metric")).toContain("/km");
    expect(formatVelocity(1.4, "walk", "metric")).toContain("/km");
  });
});

describe("formatDelta", () => {
  it("marks direction with an arrow", () => {
    expect(formatDelta(12.4)).toBe("↑ 12%");
    expect(formatDelta(-8.2)).toBe("↓ 8%");
  });

  it("shows a dash rather than a meaningless 0%", () => {
    expect(formatDelta(0.2)).toBe("—");
  });

  it("returns null when there is no baseline to compare against", () => {
    expect(formatDelta(null)).toBeNull();
    expect(formatDelta(Number.NaN)).toBeNull();
  });
});

describe("sportLabel", () => {
  it("labels known groups", () => {
    expect(sportLabel("run")).toBe("Run");
    expect(sportLabel("walk")).toBe("Walk & hike");
  });

  it("passes through an unknown group rather than dropping it", () => {
    // Unknown sports are never dropped (CLAUDE.md §4.7).
    expect(sportLabel("quidditch")).toBe("quidditch");
  });
});
