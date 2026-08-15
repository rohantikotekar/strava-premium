/**
 * The chart registry.
 *
 * Each chart declares what data it needs. The dashboard renders the registry
 * **filtered by the user's capabilities** — a chart with no backing data is never
 * drawn as an empty chart; it disappears or moves to "Unlock more" with an
 * explanation of what would enable it (CLAUDE.md §5).
 */

import type { CapabilitiesResponse } from "@/lib/api";

export interface ChartDef {
  id: string;
  title: string;
  /** The plain-language question this chart answers. */
  question: string;
  /** Capability strings that must all be present. */
  requires: string[];
  /** Minimum activities carrying the required capability. */
  minCoverage?: number;
  /** Minimum span of history, in days. */
  minSpanDays?: number;
  /** Shown in "Unlock more" when `requires` isn't met. */
  unlockHint: string;
  /** Which page it belongs on. */
  section: "dashboard" | "progress";
  /** Grid width. */
  span?: 1 | 2;
}

export const CHART_REGISTRY: ChartDef[] = [
  {
    id: "fitness",
    title: "Fitness & freshness",
    question: "Am I building fitness, and am I fresh?",
    requires: [],
    minCoverage: 10,
    unlockHint: "Import a few weeks of activities to see your fitness curve.",
    section: "dashboard",
    span: 2,
  },
  {
    id: "calendar",
    title: "Training calendar",
    question: "How consistent have I been?",
    requires: [],
    minCoverage: 5,
    unlockHint: "Import your history to see your training calendar.",
    section: "dashboard",
    span: 2,
  },
  {
    id: "weekly-volume",
    title: "Weekly volume",
    question: "How much am I training?",
    requires: [],
    minCoverage: 3,
    unlockHint: "A few more activities and we can show weekly trends.",
    section: "dashboard",
    span: 1,
  },
  {
    id: "sport-mix",
    title: "Sport mix",
    question: "What am I actually spending time on?",
    // Only meaningful with more than one sport — a single-sport athlete gets a
    // useless one-colour chart otherwise.
    requires: [],
    minCoverage: 5,
    unlockHint: "Record more than one sport to see how your time splits.",
    section: "dashboard",
    span: 1,
  },
  {
    id: "year-over-year",
    title: "Year over year",
    question: "Am I ahead of where I was?",
    requires: [],
    minSpanDays: 400,
    unlockHint:
      "Import at least two years of history to compare this year against last.",
    section: "progress",
    span: 2,
  },
  {
    id: "records",
    title: "Personal records",
    question: "What are my best efforts?",
    requires: ["stream.distance"],
    unlockHint:
      "Records come from detailed GPS files. They appear once we've analysed your .fit/.gpx data.",
    section: "progress",
    span: 1,
  },
  {
    id: "hr-zones",
    title: "Heart-rate zones",
    question: "Where is my training time actually going?",
    requires: ["stream.heartrate"],
    minCoverage: 3,
    unlockHint:
      "Wear a heart-rate monitor and we'll show training zones, TRIMP load and aerobic decoupling.",
    section: "progress",
    span: 1,
  },
  {
    id: "power-curve",
    title: "Power curve",
    question: "What can I sustain, and for how long?",
    requires: ["stream.power"],
    minCoverage: 3,
    unlockHint:
      "Ride with a power meter and we'll build your power curve and critical-power estimates.",
    section: "progress",
    span: 2,
  },
  {
    id: "pace-curve",
    title: "Pace curve",
    question: "What pace can I hold, and for how long?",
    requires: ["stream.speed"],
    minCoverage: 3,
    unlockHint: "Pace curves appear once we've analysed your detailed GPS files.",
    section: "progress",
    span: 2,
  },
  {
    id: "gear",
    title: "Gear mileage",
    question: "When do I need new shoes?",
    requires: ["field.gear"],
    unlockHint:
      "Tag your activities with gear on Strava and we'll track mileage and tell you when to replace it.",
    section: "progress",
    span: 1,
  },
];

export interface CapabilityIndex {
  has(capability: string): boolean;
  countFor(capability: string): number;
  totalActivities: number;
  spanDays: number;
  sports: string[];
}

export function buildCapabilityIndex(data: CapabilitiesResponse | undefined): CapabilityIndex {
  const counts = new Map<string, number>();
  for (const entry of data?.capabilities ?? []) {
    counts.set(entry.capability, entry.activity_count);
  }

  let spanDays = 0;
  if (data?.first_activity && data?.last_activity) {
    spanDays =
      (new Date(data.last_activity).getTime() - new Date(data.first_activity).getTime()) /
      86_400_000;
  }

  return {
    has: (capability) => counts.has(capability),
    countFor: (capability) => counts.get(capability) ?? 0,
    totalActivities: data?.total_activities ?? 0,
    spanDays,
    sports: data?.sports ?? [],
  };
}

export interface ChartAvailability {
  available: ChartDef[];
  locked: ChartDef[];
}

/** Split the registry into what we can draw and what we can explain. */
export function resolveCharts(
  index: CapabilityIndex,
  section: ChartDef["section"],
): ChartAvailability {
  const available: ChartDef[] = [];
  const locked: ChartDef[] = [];

  for (const chart of CHART_REGISTRY) {
    if (chart.section !== section) continue;

    const hasRequired = chart.requires.every((capability) => index.has(capability));
    const coverage = chart.requires.length
      ? Math.max(...chart.requires.map((c) => index.countFor(c)))
      : index.totalActivities;

    const meetsCoverage = coverage >= (chart.minCoverage ?? 1);
    const meetsSpan = index.spanDays >= (chart.minSpanDays ?? 0);
    // Sport mix is pointless with one sport — hide rather than draw one block.
    const meetsSpecial = chart.id !== "sport-mix" || index.sports.length > 1;

    if (hasRequired && meetsCoverage && meetsSpan && meetsSpecial) {
      available.push(chart);
    } else if (!hasRequired) {
      // Only surface as "unlockable" when it's a *data* gap the user could close,
      // not merely "you haven't trained enough yet".
      locked.push(chart);
    }
  }

  return { available, locked };
}
