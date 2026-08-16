import {
  Badge,
  Button,
  Card,
  ChartSkeleton,
  EmptyState,
  InfoDot,
} from "@/components/ui/primitives";
import { useCurrentUser } from "@/features/auth/useAuth";
import { ZonesChart } from "@/features/charts/charts";
import {
  type ActivityDetail,
  type ActivityPage,
  type ChartResponse,
  type StreamsResponse,
  api,
} from "@/lib/api";
import {
  type UnitPref,
  formatClock,
  formatDateTime,
  formatDistance,
  formatDuration,
  formatElevation,
  formatNumber,
  formatVelocity,
  sportLabel,
} from "@/lib/format";
import { glossary } from "@/lib/glossary";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";

export function ActivitiesPage() {
  const [sport, setSport] = useState("all");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const { data: user } = useCurrentUser();
  const pref: UnitPref = user?.measurement_pref ?? "metric";

  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (sport !== "all") params.set("sport", sport);
  if (search) params.set("search", search);

  const { data, isPending } = useQuery({
    queryKey: ["activities", sport, search, offset],
    queryFn: () => api.get<ActivityPage>(`/activities?${params}`),
  });

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-3xl font-bold tracking-tight text-[var(--text-primary)]">Activities</h1>
        <div className="flex gap-2">
          <input
            type="search"
            placeholder="Search by name…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm"
          />
          <select
            value={sport}
            onChange={(e) => {
              setSport(e.target.value);
              setOffset(0);
            }}
            aria-label="Sport"
            className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm"
          >
            <option value="all">All sports</option>
            {["run", "ride", "swim", "walk", "gym", "ski", "water", "other"].map((group) => (
              <option key={group} value={group}>
                {sportLabel(group)}
              </option>
            ))}
          </select>
        </div>
      </header>

      <Card>
        {isPending && <ChartSkeleton height={320} />}

        {!isPending && data && data.items.length === 0 && (
          <EmptyState title="Nothing matches that.">
            Try clearing the search or picking a different sport.
          </EmptyState>
        )}

        {!isPending && data && data.items.length > 0 && (
          <>
            <div className="scroll-x">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] text-left">
                    {["Activity", "Date", "Distance", "Time", "Pace/Speed"].map((h) => (
                      <th
                        key={h}
                        className="py-2.5 pr-3 text-sm font-medium text-[var(--text-secondary)]"
                      >
                        {h}
                      </th>
                    ))}
                    <th className="py-2.5 pr-3 text-sm font-medium text-[var(--text-secondary)]">
                      <span className="flex items-center gap-1.5">
                        Load
                        {(() => {
                          const entry = glossary("load_source");
                          return entry ? (
                            <InfoDot label={entry.term} what={entry.what} why={entry.why} />
                          ) : null;
                        })()}
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((activity) => (
                    <tr
                      key={activity.id}
                      className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--page)]"
                    >
                      <td className="max-w-xs py-3 pr-3">
                        <Link
                          to="/activities/$activityId"
                          params={{ activityId: activity.id }}
                          className="block truncate font-medium text-[var(--text-primary)] hover:underline"
                          title={activity.name ?? undefined}
                        >
                          {activity.name || sportLabel(activity.sport_group)}
                        </Link>
                        <span className="text-xs text-[var(--text-muted)]">
                          {activity.sport_type}
                          {activity.is_indoor && " · indoor"}
                        </span>
                      </td>
                      <td className="py-3 pr-3 text-[var(--text-secondary)]">
                        {formatDateTime(activity.start_time_local)}
                      </td>
                      <td className="py-3 pr-3 tnum text-[var(--text-primary)]">
                        {formatDistance(activity.distance_m, pref)}
                      </td>
                      <td className="py-3 pr-3 tnum text-[var(--text-primary)]">
                        {formatDuration(activity.moving_time_s ?? activity.elapsed_time_s)}
                      </td>
                      <td className="py-3 pr-3 tnum text-[var(--text-secondary)]">
                        {formatVelocity(activity.avg_speed_mps, activity.sport_group, pref)}
                      </td>
                      <td className="py-3 tnum text-[var(--text-secondary)]">
                        {formatNumber(activity.training_load)}
                        {activity.load_source &&
                          ["rpe", "duration"].includes(activity.load_source) && (
                            <span title="Estimated from duration" className="ml-1">
                              ≈
                            </span>
                          )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <nav className="mt-4 flex items-center justify-between">
              <p className="text-xs text-[var(--text-muted)]">
                {offset + 1}–{Math.min(offset + limit, data.total)} of {data.total.toLocaleString()}
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={offset + limit >= data.total}
                  onClick={() => setOffset(offset + limit)}
                >
                  Next
                </Button>
              </div>
            </nav>
          </>
        )}
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //

export function ActivityDetailPage() {
  const { activityId } = useParams({ from: "/activities/$activityId" });
  const { data: user } = useCurrentUser();
  const pref: UnitPref = user?.measurement_pref ?? "metric";

  const { data: activity, isPending } = useQuery({
    queryKey: ["activity", activityId],
    queryFn: () => api.get<ActivityDetail>(`/activities/${activityId}`),
  });

  const { data: streams } = useQuery({
    queryKey: ["activity-streams", activityId],
    queryFn: () => api.get<StreamsResponse>(`/activities/${activityId}/streams?max_points=1200`),
    enabled: Boolean(activity?.has_streams),
    retry: false,
  });

  if (isPending) return <ChartSkeleton height={400} />;
  if (!activity) return <EmptyState title="We couldn't find that activity." />;

  const duration = activity.moving_time_s ?? activity.elapsed_time_s;

  return (
    <div className="flex flex-col gap-5">
      <header>
        <Link to="/activities" className="text-sm text-[var(--text-secondary)] hover:underline">
          ← Back to activities
        </Link>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-[var(--text-primary)]">
          {activity.name || sportLabel(activity.sport_group)}
        </h1>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          {activity.sport_type} · {formatDateTime(activity.start_time_local)}
          {activity.strava_activity_id && (
            <>
              {" · "}
              <a
                href={`https://www.strava.com/activities/${activity.strava_activity_id}`}
                target="_blank"
                rel="noreferrer noopener"
                className="underline"
              >
                View on Strava ↗
              </a>
            </>
          )}
        </p>
      </header>

      <Card>
        {/* Hero: the number this activity is about. */}
        <p className="text-4xl font-semibold text-[var(--text-primary)]">
          {formatDistance(activity.distance_m, pref)}
        </p>
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Moving time" value={formatDuration(duration)} />
          <Stat
            label="Pace"
            value={formatVelocity(activity.avg_speed_mps, activity.sport_group, pref)}
          />
          <Stat label="Elevation" value={formatElevation(activity.elevation_gain_m, pref)} />
          <Stat
            label="Avg heart rate"
            value={activity.avg_hr_bpm ? `${Math.round(activity.avg_hr_bpm)} bpm` : "—"}
          />
          {activity.avg_power_w !== null && (
            <Stat label="Avg power" value={`${Math.round(activity.avg_power_w)} W`} />
          )}
          {activity.normalized_power_w !== null && (
            <Stat
              label="Normalized power"
              value={`${Math.round(activity.normalized_power_w)} W`}
              glossaryKey="np"
            />
          )}
          {activity.training_load !== null && (
            <Stat
              label="Training load"
              value={formatNumber(activity.training_load)}
              glossaryKey="load_source"
              badge={
                activity.load_source && ["rpe", "duration"].includes(activity.load_source)
                  ? "Estimated"
                  : undefined
              }
            />
          )}
          {activity.decoupling_pct !== null && (
            <Stat
              label="Decoupling"
              value={`${activity.decoupling_pct.toFixed(1)}%`}
              glossaryKey="decoupling"
            />
          )}
        </div>
      </Card>

      {!activity.has_streams && (
        <Card>
          <EmptyState title="No detailed data for this one.">
            This activity came from the summary index only. If its .fit or .gpx file was in your
            export, deeper charts will appear once we've finished analysing it.
          </EmptyState>
        </Card>
      )}

      {streams && <StreamPanels streams={streams} pref={pref} sport={activity.sport_group} />}

      {Object.keys(activity.zone_time.hr ?? {}).length > 0 && (
        <Card>
          <h2 className="mb-3 text-base font-semibold text-[var(--text-primary)]">
            Heart-rate zones
          </h2>
          <ZonesChart
            data={
              {
                meta: {},
                series: [
                  {
                    key: "hr",
                    label: "Time in zone",
                    points: Object.entries(activity.zone_time.hr ?? {}).map(([zone, seconds]) => ({
                      zone: Number(zone),
                      label: `Zone ${zone}`,
                      seconds,
                    })),
                  },
                ],
              } as unknown as ChartResponse
            }
          />
        </Card>
      )}

      {Object.keys(activity.distance_prs).length > 0 && (
        <Card>
          <h2 className="mb-3 text-base font-semibold text-[var(--text-primary)]">
            Best efforts in this activity
          </h2>
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(activity.distance_prs)
                .sort((a, b) => Number(a[0]) - Number(b[0]))
                .map(([distance, seconds]) => (
                  <tr key={distance} className="border-b border-[var(--border)] last:border-0">
                    <td className="py-2 text-[var(--text-primary)]">
                      {formatDistance(Number(distance), pref)}
                    </td>
                    <td className="py-2 text-right tnum text-[var(--text-primary)]">
                      {formatClock(seconds)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  glossaryKey,
  badge,
}: {
  label: string;
  value: string;
  glossaryKey?: string;
  badge?: string;
}) {
  const entry = glossaryKey ? glossary(glossaryKey) : undefined;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
        {label}
        {entry && <InfoDot label={entry.term} what={entry.what} why={entry.why} />}
      </span>
      <span className="text-lg font-semibold text-[var(--text-primary)]">{value}</span>
      {badge && <Badge tone="estimate">{badge}</Badge>}
    </div>
  );
}

/**
 * Stacked panels sharing one x-axis — never overlaid on two y-scales.
 * Pace, heart rate and elevation have unrelated scales; a dual axis would
 * manufacture correlations that aren't in the data.
 */
function StreamPanels({
  streams,
  pref,
  sport,
}: {
  streams: StreamsResponse;
  pref: UnitPref;
  sport: string;
}) {
  interface Panel {
    key: string;
    label: string;
    color: string;
    format: (value: number) => string;
  }

  const allPanels: Panel[] = [
    {
      key: "speed_mps",
      label: sport === "run" ? "Pace" : "Speed",
      color: "var(--series-1)",
      format: (value: number) => formatVelocity(value, sport, pref),
    },
    {
      key: "heartrate_bpm",
      label: "Heart rate",
      color: "var(--series-2)",
      format: (value: number) => `${Math.round(value)} bpm`,
    },
    {
      key: "power_w",
      label: "Power",
      color: "var(--series-7)",
      format: (value: number) => `${Math.round(value)} W`,
    },
    {
      key: "altitude_m",
      label: "Elevation",
      color: "var(--series-3)",
      format: (value: number) => formatElevation(value, pref),
    },
  ];

  const panels = allPanels.filter((panel) =>
    streams.channels[panel.key]?.some((value) => value !== null),
  );

  if (panels.length === 0) return null;

  return (
    <Card>
      <h2 className="mb-1 text-base font-semibold text-[var(--text-primary)]">
        Through the activity
      </h2>
      <p className="mb-4 text-xs text-[var(--text-secondary)]">
        Each metric gets its own panel on a shared time axis.
      </p>
      <div className="flex flex-col gap-4">
        {panels.map((panel) => (
          <StreamPanel
            key={panel.key}
            label={panel.label}
            color={panel.color}
            values={streams.channels[panel.key] ?? []}
            format={panel.format}
          />
        ))}
      </div>
    </Card>
  );
}

function StreamPanel({
  label,
  color,
  values,
  format,
}: {
  label: string;
  color: string;
  values: (number | null)[];
  format: (v: number) => string;
}) {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (finite.length === 0) return null;

  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min || 1;
  const width = 800;
  const height = 70;

  // Gaps in the data stay gaps in the line — never interpolated across a pause.
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    const x = (index / Math.max(values.length - 1, 1)) * width;
    const y = height - ((value - min) / span) * height;
    current.push(`${current.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));

  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="flex items-baseline justify-between text-xs">
        <span className="flex items-center gap-1.5 text-[var(--text-secondary)]">
          <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: color }} />
          {label}
        </span>
        <span className="tnum text-[var(--text-muted)]">
          {format(min)} – {format(max)}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-[70px] w-full"
        role="img"
        aria-label={`${label} over time, ranging ${format(min)} to ${format(max)}`}
      >
        {segments.map((path) => (
          <path key={path.slice(0, 24)} d={path} fill="none" stroke={color} strokeWidth={2} />
        ))}
      </svg>
    </figure>
  );
}
