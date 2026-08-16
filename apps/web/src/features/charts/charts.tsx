/**
 * Chart visuals.
 *
 * Binding rules enforced here (FRONTEND_DESIGN.md § chart system):
 *  - never a dual y-axis; two scales become two stacked panels sharing an x-axis
 *  - categorical colour follows the *entity* via a fixed slot map, never rank
 *  - zones use the ordinal sequential ramp, because zones are ordered
 *  - year-over-year is emphasis (one hue + grey), not ten categorical hues
 */

import type { ChartResponse } from "@/lib/api";
import {
  type UnitPref,
  formatClock,
  formatDistance,
  formatDuration,
  sportLabel,
} from "@/lib/format";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const AXIS = { fill: "var(--text-muted)", fontSize: 11 };
const GRID = "var(--gridline)";

const tooltipStyle = {
  contentStyle: {
    background: "var(--surface-1)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 12,
    color: "var(--text-primary)",
  },
  labelStyle: { color: "var(--text-secondary)" },
};

/**
 * Sport -> palette slot. Fixed, so filtering out "Ride" never repaints "Run".
 * Colour follows the entity, never its rank in a filtered list.
 */
const SPORT_SLOT: Record<string, string> = {
  run: "var(--series-1)",
  ride: "var(--series-2)",
  swim: "var(--series-3)",
  walk: "var(--series-4)",
  gym: "var(--series-5)",
  ski: "var(--series-6)",
  water: "var(--series-7)",
  other: "var(--series-8)",
};

/** Ordinal ramp for zones: light -> dark as intensity rises. */
const ZONE_RAMP = [
  "var(--seq-250)",
  "var(--seq-400)",
  "var(--seq-550)",
  "var(--seq-700)",
  "var(--series-8)",
  "var(--series-2)",
  "var(--series-5)",
];

// --------------------------------------------------------------------------- //

export function FitnessChart({ data }: { data: ChartResponse }) {
  const points = data.series[0]?.points ?? [];

  return (
    <div className="flex flex-col gap-1">
      {/* Panel 1: fitness (area) + fatigue (line). Same unit, so one axis. */}
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="day"
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "var(--baseline)" }}
            minTickGap={40}
          />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={38} />
          <Tooltip {...tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
          <Area
            type="monotone"
            dataKey="ctl"
            name="Fitness"
            stroke="var(--series-1)"
            fill="var(--series-1)"
            fillOpacity={0.15}
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="atl"
            name="Fatigue"
            stroke="var(--series-2)"
            strokeWidth={2}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Panel 2: form, on its own scale. A second y-axis here would invent a
          correlation that isn't there — so it gets its own panel instead. */}
      <ResponsiveContainer width="100%" height={90}>
        <BarChart data={points} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="day"
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "var(--baseline)" }}
            minTickGap={40}
          />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={38} />
          <Tooltip {...tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
          {/* Diverging: fresh above the line, fatigued below. */}
          <Bar dataKey="tsb" name="Form" radius={[2, 2, 0, 0]}>
            {points.map((point) => (
              <Cell
                key={String(point.day)}
                fill={Number(point.tsb ?? 0) >= 0 ? "var(--series-1)" : "var(--series-8)"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WeeklyVolumeChart({ data, pref }: { data: ChartResponse; pref: UnitPref }) {
  const points = data.series[0]?.points ?? [];

  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis
          dataKey="week"
          tick={AXIS}
          tickLine={false}
          axisLine={{ stroke: "var(--baseline)" }}
          minTickGap={30}
        />
        <YAxis
          tick={AXIS}
          tickLine={false}
          axisLine={false}
          width={48}
          tickFormatter={(v) => formatDistance(Number(v), pref)}
        />
        <Tooltip {...tooltipStyle} formatter={(v) => formatDistance(Number(v), pref)} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        <Bar
          dataKey="distance_m"
          name="Weekly distance"
          fill="var(--series-1)"
          radius={[4, 4, 0, 0]}
        />
        {/* Same unit as the bars, so it shares the one axis — no second scale. */}
        <Line
          type="monotone"
          dataKey="rolling_4w_m"
          name="4-week average"
          stroke="var(--seq-700)"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function SportMixChart({ data }: { data: ChartResponse }) {
  const points = data.series[0]?.points ?? [];
  const sports = [...new Set(points.flatMap((p) => Object.keys(p).filter((k) => k !== "month")))];

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis
          dataKey="month"
          tick={AXIS}
          tickLine={false}
          axisLine={{ stroke: "var(--baseline)" }}
          minTickGap={30}
        />
        <YAxis
          tick={AXIS}
          tickLine={false}
          axisLine={false}
          width={48}
          tickFormatter={(v) => formatDuration(Number(v))}
        />
        <Tooltip {...tooltipStyle} formatter={(v) => formatDuration(Number(v))} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        {sports.map((sport) => (
          <Bar
            key={sport}
            dataKey={sport}
            name={sportLabel(sport)}
            stackId="sport"
            fill={SPORT_SLOT[sport] ?? "var(--series-8)"}
            // 2px surface gap between stacked segments.
            stroke="var(--surface-1)"
            strokeWidth={2}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function YearOverYearChart({ data, pref }: { data: ChartResponse; pref: UnitPref }) {
  const currentYear = String(new Date().getFullYear());
  // Merge every year's series onto one day-of-year axis.
  const merged = new Map<number, Record<string, number>>();
  for (const series of data.series) {
    for (const point of series.points) {
      const doy = Number(point.doy);
      const row = merged.get(doy) ?? { doy };
      row[series.key] = Number(point.distance_m);
      merged.set(doy, row);
    }
  }
  const rows = [...merged.values()].sort((a, b) => (a.doy ?? 0) - (b.doy ?? 0));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis
          dataKey="doy"
          type="number"
          domain={[1, 366]}
          tick={AXIS}
          tickLine={false}
          axisLine={{ stroke: "var(--baseline)" }}
          tickFormatter={(v) =>
            new Date(2024, 0, Number(v)).toLocaleDateString(undefined, { month: "short" })
          }
        />
        <YAxis
          tick={AXIS}
          tickLine={false}
          axisLine={false}
          width={48}
          tickFormatter={(v) => formatDistance(Number(v), pref)}
        />
        <Tooltip {...tooltipStyle} formatter={(v) => formatDistance(Number(v), pref)} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        {/* Emphasis, not categorical: this year in the accent hue, prior years
            in the de-emphasis grey. Ten hues would bury the one line that matters. */}
        {data.series.map((series) => (
          <Line
            key={series.key}
            type="monotone"
            dataKey={series.key}
            name={series.label}
            stroke={series.key === currentYear ? "var(--series-1)" : "var(--de-emphasis)"}
            strokeWidth={series.key === currentYear ? 2.5 : 1.5}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ZonesChart({ data }: { data: ChartResponse }) {
  const points = data.series[0]?.points ?? [];
  const total = points.reduce((sum, p) => sum + Number(p.seconds ?? 0), 0);

  return (
    <div className="flex flex-col gap-3">
      {/* One stacked horizontal bar. Ordinal ramp: zones are ordered, so five
          different hues would destroy the ordering. */}
      <div
        className="flex h-8 w-full overflow-hidden rounded-md"
        role="img"
        aria-label="Time in zone"
      >
        {points.map((point, index) => {
          const seconds = Number(point.seconds ?? 0);
          const pct = total > 0 ? (seconds / total) * 100 : 0;
          if (pct <= 0) return null;
          return (
            <div
              key={String(point.zone)}
              style={{
                width: `${pct}%`,
                background: ZONE_RAMP[index] ?? "var(--seq-400)",
                borderRight: "2px solid var(--surface-1)",
              }}
              title={`${point.label}: ${formatDuration(seconds)}`}
            />
          );
        })}
      </div>

      {/* Direct labels: these palette slots sit below 3:1 on the light surface,
          so the wrapper requires visible labels rather than colour alone. */}
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        {points.map((point, index) => (
          <li key={String(point.zone)} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ background: ZONE_RAMP[index] ?? "var(--seq-400)" }}
            />
            <span className="text-[var(--text-secondary)]">{String(point.label)}</span>
            <span className="ml-auto tnum text-[var(--text-primary)]">
              {formatDuration(Number(point.seconds ?? 0))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CurveChart({ data, unit }: { data: ChartResponse; unit: string }) {
  const allTime = data.series.find((s) => s.key === "all_time")?.points ?? [];
  const selected = data.series.find((s) => s.key === "selected")?.points ?? [];

  const merged = new Map<number, Record<string, number>>();
  for (const point of allTime) {
    const duration = Number(point.duration_s);
    merged.set(duration, { duration_s: duration, all_time: Number(point.value) });
  }
  for (const point of selected) {
    const duration = Number(point.duration_s);
    const row = merged.get(duration) ?? { duration_s: duration };
    row.selected = Number(point.value);
    merged.set(duration, row);
  }
  const rows = [...merged.values()].sort((a, b) => (a.duration_s ?? 0) - (b.duration_s ?? 0));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        {/* Log x: the curve bends hardest in the first minute, and a linear axis
            would compress that entire region into a few pixels. */}
        <XAxis
          dataKey="duration_s"
          type="number"
          scale="log"
          domain={["dataMin", "dataMax"]}
          tick={AXIS}
          tickLine={false}
          axisLine={{ stroke: "var(--baseline)" }}
          tickFormatter={(v) => formatClock(Number(v))}
        />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} unit={unit} />
        <Tooltip {...tooltipStyle} labelFormatter={(v) => `Duration ${formatClock(Number(v))}`} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        <Line
          type="monotone"
          dataKey="all_time"
          name="All time"
          stroke="var(--de-emphasis)"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="selected"
          name="Selected period"
          stroke="var(--series-1)"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CalendarHeatmap({ data }: { data: ChartResponse }) {
  const points = data.series[0]?.points ?? [];
  if (points.length === 0) return null;

  const loads = points.map((p) => Number(p.load ?? 0)).filter((v) => v > 0);
  // p95 rather than max: one 6-hour ultra shouldn't flatten the whole year.
  const sorted = [...loads].sort((a, b) => a - b);
  const p95 = sorted.length ? (sorted[Math.floor(sorted.length * 0.95)] ?? 1) : 1;

  const CELL = 11;
  const GAP = 3;
  const first = new Date(String(points[0]?.day));
  const startOffset = (first.getDay() + 6) % 7; // Monday-first grid

  const cells = points.map((point, index) => {
    const load = Number(point.load ?? 0);
    const intensity = load <= 0 ? 0 : Math.min(load / p95, 1);
    const step =
      intensity === 0
        ? "var(--page)"
        : intensity < 0.25
          ? "var(--seq-100)"
          : intensity < 0.5
            ? "var(--seq-250)"
            : intensity < 0.75
              ? "var(--seq-400)"
              : intensity < 0.95
                ? "var(--seq-550)"
                : "var(--seq-700)";

    const slot = index + startOffset;
    return {
      x: Math.floor(slot / 7) * (CELL + GAP),
      y: (slot % 7) * (CELL + GAP),
      fill: step,
      day: String(point.day),
      load,
      activities: Number(point.activities ?? 0),
    };
  });

  const width = Math.ceil((points.length + startOffset) / 7) * (CELL + GAP);

  return (
    <div className="scroll-x">
      <svg
        width={width}
        height={7 * (CELL + GAP)}
        role="img"
        aria-label={`Training calendar: ${loads.length} active days out of ${points.length}`}
      >
        <title>Daily training load</title>
        {cells.map((cell) => (
          <rect
            key={cell.day}
            x={cell.x}
            y={cell.y}
            width={CELL}
            height={CELL}
            rx={2}
            fill={cell.fill}
            stroke="var(--border)"
            strokeWidth={0.5}
          >
            <title>{`${cell.day}: ${cell.activities} activit${
              cell.activities === 1 ? "y" : "ies"
            }, load ${Math.round(cell.load)}`}</title>
          </rect>
        ))}
      </svg>
    </div>
  );
}

export function GearChart({ data, pref }: { data: ChartResponse; pref: UnitPref }) {
  const points = data.series[0]?.points ?? [];
  const max = Math.max(...points.map((p) => Number(p.distance_m ?? 0)), 1);

  return (
    <ul className="flex flex-col gap-3">
      {points.map((point) => {
        const distance = Number(point.distance_m ?? 0);
        const alertAt = point.alert_at_m ? Number(point.alert_at_m) : null;
        const overdue = alertAt !== null && distance >= alertAt;
        return (
          <li key={String(point.id)} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="truncate text-[var(--text-primary)]" title={String(point.name)}>
                {String(point.name)}
              </span>
              <span className="shrink-0 tnum text-[var(--text-secondary)]">
                {formatDistance(distance, pref)}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--page)]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(distance / max) * 100}%`,
                  background: overdue ? "var(--status-warning)" : "var(--series-1)",
                }}
              />
            </div>
            {overdue && (
              // Status colour never carries meaning alone: icon + label.
              <p className="text-xs text-[var(--text-secondary)]">
                <span aria-hidden="true">! </span>
                Past your replacement threshold
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function RecordsTable({ data, pref }: { data: ChartResponse; pref: UnitPref }) {
  const points = data.series[0]?.points ?? [];

  return (
    <div className="scroll-x">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left">
            <th className="py-2 font-medium text-[var(--text-secondary)]">Distance</th>
            <th className="py-2 font-medium text-[var(--text-secondary)]">Best time</th>
            <th className="py-2 font-medium text-[var(--text-secondary)]">Pace</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => {
            const distance = Number(point.distance_m);
            const time = Number(point.time_s);
            return (
              <tr key={distance} className="border-b border-[var(--border)] last:border-0">
                <td className="py-2 text-[var(--text-primary)]">
                  {formatDistance(distance, pref)}
                </td>
                <td className="py-2 tnum text-[var(--text-primary)]">{formatClock(time)}</td>
                <td className="py-2 tnum text-[var(--text-secondary)]">
                  {formatClock(time / (distance / 1000))} /km
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
