import type { CapabilitiesResponse } from "@/lib/api";
import { describe, expect, it } from "vitest";
import { buildCapabilityIndex, resolveCharts } from "./registry";

/**
 * The capability model (CLAUDE.md §5). A chart with no backing data is never
 * rendered as an empty chart — it disappears or becomes an "unlock" card.
 */
function capabilities(
  entries: [string, number][],
  opts: { first?: string; last?: string; total?: number; sports?: string[] } = {},
): CapabilitiesResponse {
  return {
    capabilities: entries.map(([capability, activity_count]) => ({
      capability,
      activity_count,
      first_seen: opts.first ?? "2024-01-01",
      last_seen: opts.last ?? "2024-06-01",
    })),
    total_activities: opts.total ?? 100,
    first_activity: opts.first ?? "2024-01-01",
    last_activity: opts.last ?? "2024-06-01",
    sports: opts.sports ?? ["run"],
  };
}

describe("buildCapabilityIndex", () => {
  it("reports presence and counts", () => {
    const index = buildCapabilityIndex(capabilities([["stream.heartrate", 42]]));
    expect(index.has("stream.heartrate")).toBe(true);
    expect(index.countFor("stream.heartrate")).toBe(42);
    expect(index.has("stream.power")).toBe(false);
    expect(index.countFor("stream.power")).toBe(0);
  });

  it("computes the span of history in days", () => {
    const index = buildCapabilityIndex(
      capabilities([], { first: "2024-01-01", last: "2024-01-31" }),
    );
    expect(index.spanDays).toBe(30);
  });

  it("handles undefined data without throwing", () => {
    const index = buildCapabilityIndex(undefined);
    expect(index.totalActivities).toBe(0);
    expect(index.has("anything")).toBe(false);
  });
});

describe("resolveCharts", () => {
  it("hides the power curve for an athlete with no power meter", () => {
    // This is the whole point: 90% of users have never owned a power meter.
    const index = buildCapabilityIndex(capabilities([["stream.heartrate", 50]]));
    const { available, locked } = resolveCharts(index, "progress");

    expect(available.map((c) => c.id)).not.toContain("power-curve");
    expect(locked.map((c) => c.id)).toContain("power-curve");
  });

  it("shows the power curve once power data exists", () => {
    const index = buildCapabilityIndex(
      capabilities([
        ["stream.power", 20],
        ["stream.heartrate", 50],
      ]),
    );
    const { available } = resolveCharts(index, "progress");
    expect(available.map((c) => c.id)).toContain("power-curve");
  });

  it("gives every locked chart an actionable unlock hint", () => {
    const index = buildCapabilityIndex(capabilities([]));
    const { locked } = resolveCharts(index, "progress");

    expect(locked.length).toBeGreaterThan(0);
    for (const chart of locked) {
      expect(chart.unlockHint.length).toBeGreaterThan(20);
    }
  });

  it("hides year-over-year until there are two years of history", () => {
    const short = buildCapabilityIndex(
      capabilities([], { first: "2024-01-01", last: "2024-06-01" }),
    );
    expect(resolveCharts(short, "progress").available.map((c) => c.id)).not.toContain(
      "year-over-year",
    );

    const long = buildCapabilityIndex(
      capabilities([], { first: "2022-01-01", last: "2024-06-01" }),
    );
    expect(resolveCharts(long, "progress").available.map((c) => c.id)).toContain("year-over-year");
  });

  it("hides sport mix for a single-sport athlete", () => {
    // One block of one colour is not a chart.
    const single = buildCapabilityIndex(capabilities([], { sports: ["run"] }));
    expect(resolveCharts(single, "dashboard").available.map((c) => c.id)).not.toContain(
      "sport-mix",
    );

    const multi = buildCapabilityIndex(capabilities([], { sports: ["run", "ride"] }));
    expect(resolveCharts(multi, "dashboard").available.map((c) => c.id)).toContain("sport-mix");
  });

  it("hides trend charts for a brand-new account", () => {
    const fresh = buildCapabilityIndex(capabilities([], { total: 2 }));
    const { available } = resolveCharts(fresh, "dashboard");
    // A delta against a 2-activity baseline is noise.
    expect(available.map((c) => c.id)).not.toContain("fitness");
  });

  it("does not list a chart as both available and locked", () => {
    const index = buildCapabilityIndex(
      capabilities([
        ["stream.power", 20],
        ["stream.heartrate", 50],
        ["stream.distance", 50],
        ["stream.speed", 50],
        ["field.gear", 10],
      ]),
    );
    for (const section of ["dashboard", "progress"] as const) {
      const { available, locked } = resolveCharts(index, section);
      const overlap = available.filter((a) => locked.some((l) => l.id === a.id));
      expect(overlap).toEqual([]);
    }
  });
});
