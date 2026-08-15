import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  Badge,
  Banner,
  Button,
  Card,
  ChartSkeleton,
  EmptyState,
  InfoDot,
} from "@/components/ui/primitives";
import { useCurrentUser } from "@/features/auth/useAuth";
import { ChartCard, UnlockCard } from "@/features/charts/ChartCard";
import {
  CalendarHeatmap,
  FitnessChart,
  SportMixChart,
  WeeklyVolumeChart,
} from "@/features/charts/charts";
import { buildCapabilityIndex, resolveCharts } from "@/features/charts/registry";
import { type CapabilitiesResponse, type DashboardSummary, api } from "@/lib/api";
import {
  type UnitPref,
  formatByUnit,
  formatDelta,
  formatDistance,
  sportLabel,
} from "@/lib/format";
import { glossary } from "@/lib/glossary";

const RANGES = [
  { key: "4w", label: "4 weeks" },
  { key: "3m", label: "3 months" },
  { key: "6m", label: "6 months" },
  { key: "1y", label: "1 year" },
  { key: "all", label: "All time" },
];

export function DashboardPage() {
  const [range, setRange] = useState("3m");
  const [sport, setSport] = useState("all");

  const { data: user } = useCurrentUser();
  const pref: UnitPref = user?.measurement_pref ?? "metric";

  const { data: capabilities, isPending: capsPending } = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api.get<CapabilitiesResponse>("/me/capabilities"),
  });

  const { data: summary } = useQuery({
    queryKey: ["dashboard-summary", range, sport],
    queryFn: () =>
      api.get<DashboardSummary>(
        `/charts/dashboard-summary?range=${range}${sport !== "all" ? `&sport=${sport}` : ""}`,
      ),
  });

  const index = buildCapabilityIndex(capabilities);
  const { available, locked } = resolveCharts(index, "dashboard");

  if (capsPending) {
    return (
      <div className="flex flex-col gap-4">
        <ChartSkeleton height={120} />
        <ChartSkeleton height={280} />
      </div>
    );
  }

  // A brand-new account gets a route into the product, not an empty dashboard.
  if (index.totalActivities === 0) {
    return (
      <Card>
        <EmptyState
          title="Let's get your history in here."
          action={
            <Link to="/import">
              <Button>Import my history</Button>
            </Link>
          }
        >
          Upload the export Strava emailed you and we'll build your fitness curve, personal
          records, training calendar and more. It usually takes under a minute to become
          useful.
        </EmptyState>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Filters live in one row and apply to the whole page. */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={range}
          onChange={(e) => setRange(e.target.value)}
          aria-label="Time range"
          className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm"
        >
          {RANGES.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>

        {index.sports.length > 1 && (
          <select
            value={sport}
            onChange={(e) => setSport(e.target.value)}
            aria-label="Sport"
            className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm"
          >
            <option value="all">All sports</option>
            {index.sports.map((group) => (
              <option key={group} value={group}>
                {sportLabel(group)}
              </option>
            ))}
          </select>
        )}
      </div>

      {summary && <SummaryTiles summary={summary} pref={pref} />}

      {index.totalActivities < 7 && (
        <Banner tone="info" title="Still early days">
          A few more activities and we can start showing trends. Everything below will get
          richer as your history fills in.
        </Banner>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {available.map((chart) => (
          <ChartCard
            key={chart.id}
            chartId={chart.id}
            title={chart.title}
            question={chart.question}
            range={range}
            sport={sport}
            className={chart.span === 2 ? "lg:col-span-2" : undefined}
            tableRows={
              chart.id === "weekly-volume"
                ? (data) => ({
                    headers: ["Week", "Distance", "Activities"],
                    rows: (data.series[0]?.points ?? []).map((p) => [
                      String(p.week),
                      formatDistance(Number(p.distance_m), pref),
                      Number(p.activities),
                    ]),
                  })
                : undefined
            }
          >
            {(data) => {
              switch (chart.id) {
                case "fitness":
                  return <FitnessChart data={data} />;
                case "calendar":
                  return <CalendarHeatmap data={data} />;
                case "weekly-volume":
                  return <WeeklyVolumeChart data={data} pref={pref} />;
                case "sport-mix":
                  return <SportMixChart data={data} />;
                default:
                  return null;
              }
            }}
          </ChartCard>
        ))}
      </div>

      {locked.length > 0 && (
        <Card>
          <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Unlock more</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {locked.map((chart) => (
              <UnlockCard key={chart.id} title={chart.title} hint={chart.unlockHint} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function SummaryTiles({ summary, pref }: { summary: DashboardSummary; pref: UnitPref }) {
  const heroDelta = formatDelta(summary.hero.delta_pct);

  return (
    <Card>
      <p className="text-xs text-[var(--text-secondary)]">{summary.period_label}</p>

      {/* Hero figure: proportional figures, >= 48px (dataviz § marks-and-anatomy). */}
      <p className="mt-1 text-5xl font-semibold tracking-tight text-[var(--text-primary)]">
        {formatByUnit(summary.hero.value, summary.hero.unit, pref)}
      </p>
      {heroDelta && (
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          {heroDelta} vs. the previous period
        </p>
      )}

      <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {summary.tiles.map((tile) => {
          const delta = formatDelta(tile.delta_pct);
          const entry = tile.key === "load" ? glossary("load_source") : undefined;
          return (
            <div key={tile.key} className="flex flex-col gap-0.5">
              <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                {tile.label}
                {entry && <InfoDot label={entry.term} what={entry.what} why={entry.why} />}
              </span>
              <span className="text-xl font-semibold text-[var(--text-primary)]">
                {formatByUnit(tile.value, tile.unit, pref)}
              </span>
              {delta && <span className="text-xs text-[var(--text-muted)]">{delta}</span>}
            </div>
          );
        })}
      </div>

      <div className="mt-5 flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
        <Badge>{summary.active_days} active days</Badge>
        <Badge>Current streak {summary.streak_days}d</Badge>
        <Badge>Longest streak {summary.longest_streak_days}d</Badge>
      </div>
    </Card>
  );
}
