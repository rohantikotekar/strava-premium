import { Card, ChartSkeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/features/auth/useAuth";
import { ChartCard, UnlockCard } from "@/features/charts/ChartCard";
import {
  CurveChart,
  GearChart,
  RecordsTable,
  YearOverYearChart,
  ZonesChart,
} from "@/features/charts/charts";
import { buildCapabilityIndex, resolveCharts } from "@/features/charts/registry";
import { type CapabilitiesResponse, api } from "@/lib/api";
import { type UnitPref, formatClock, formatDistance, sportLabel } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

export function ProgressPage() {
  const [range, setRange] = useState("1y");
  const [sport, setSport] = useState("all");

  const { data: user } = useCurrentUser();
  const pref: UnitPref = user?.measurement_pref ?? "metric";

  const { data: capabilities, isPending } = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api.get<CapabilitiesResponse>("/me/capabilities"),
  });

  const index = buildCapabilityIndex(capabilities);
  const { available, locked } = resolveCharts(index, "progress");

  if (isPending) return <ChartSkeleton height={400} />;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-[var(--text-primary)]">Progress</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Am I improving?</p>
        </div>
        <div className="flex gap-2">
          <select
            value={range}
            onChange={(e) => setRange(e.target.value)}
            aria-label="Time range"
            className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm"
          >
            <option value="3m">3 months</option>
            <option value="6m">6 months</option>
            <option value="1y">1 year</option>
            <option value="all">All time</option>
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
      </header>

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
              chart.id === "records"
                ? (data) => ({
                    headers: ["Distance", "Best time"],
                    rows: (data.series[0]?.points ?? []).map((p) => [
                      formatDistance(Number(p.distance_m), pref),
                      formatClock(Number(p.time_s)),
                    ]),
                  })
                : chart.id === "hr-zones"
                  ? (data) => ({
                      headers: ["Zone", "Time"],
                      rows: (data.series[0]?.points ?? []).map((p) => [
                        String(p.label),
                        formatClock(Number(p.seconds)),
                      ]),
                    })
                  : undefined
            }
          >
            {(data) => {
              switch (chart.id) {
                case "year-over-year":
                  return <YearOverYearChart data={data} pref={pref} />;
                case "records":
                  return <RecordsTable data={data} pref={pref} />;
                case "hr-zones":
                  return <ZonesChart data={data} />;
                case "power-curve":
                  return <CurveChart data={data} unit="W" />;
                case "pace-curve":
                  return <CurveChart data={data} unit=" m/s" />;
                case "gear":
                  return <GearChart data={data} pref={pref} />;
                default:
                  return null;
              }
            }}
          </ChartCard>
        ))}
      </div>

      {locked.length > 0 && (
        <Card>
          <h2 className="mb-3 text-base font-semibold text-[var(--text-primary)]">Unlock more</h2>
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
