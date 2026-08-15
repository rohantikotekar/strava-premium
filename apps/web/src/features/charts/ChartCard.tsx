/**
 * The wrapper every chart lives in.
 *
 * Handles, uniformly: loading skeleton, empty state, error + retry, the table
 * view toggle, coverage/estimate disclosure, and the direct-label enforcement for
 * low-contrast palette slots. **No chart implements those itself**
 * (FRONTEND_DESIGN.md § the chart wrapper contract).
 */

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Badge, Button, Card, ChartSkeleton, EmptyState } from "@/components/ui/primitives";
import { type ChartResponse, api } from "@/lib/api";

export function useChart(chartId: string, range: string, sport?: string) {
  const params = new URLSearchParams({ range });
  if (sport && sport !== "all") params.set("sport", sport);

  return useQuery({
    queryKey: ["chart", chartId, range, sport ?? "all"],
    queryFn: () => api.get<ChartResponse>(`/charts/${chartId}?${params}`),
    staleTime: 60_000,
  });
}

interface ChartCardProps {
  chartId: string;
  title: string;
  question: string;
  range: string;
  sport?: string;
  /** Render the visual. Only called when there is data. */
  children: (data: ChartResponse) => ReactNode;
  /** Rows for the table view — the accessibility fallback and contrast relief. */
  tableRows?: (data: ChartResponse) => { headers: string[]; rows: (string | number)[][] };
  className?: string;
}

export function ChartCard({
  chartId,
  title,
  question,
  range,
  sport,
  children,
  tableRows,
  className,
}: ChartCardProps) {
  const [showTable, setShowTable] = useState(false);
  const { data, isPending, isError, error, refetch } = useChart(chartId, range, sport);

  const isEmpty =
    !isPending && !isError && (!data || data.series.every((s) => s.points.length === 0));

  return (
    <Card className={className}>
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h2>
          {/* The question comes before the chart — the chart is the evidence. */}
          <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{question}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {data?.meta.is_estimate && <Badge tone="estimate">Estimated</Badge>}
          {tableRows && !isEmpty && !isPending && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowTable((v) => !v)}
              aria-pressed={showTable}
            >
              {showTable ? "Chart" : "Table"}
            </Button>
          )}
        </div>
      </header>

      {isPending && <ChartSkeleton />}

      {isError && (
        <EmptyState title="We couldn't load this chart.">
          <span className="block">{(error as Error).message}</span>
          <Button size="sm" variant="secondary" className="mt-3" onClick={() => refetch()}>
            Try again
          </Button>
        </EmptyState>
      )}

      {isEmpty && (
        <EmptyState title="Nothing here yet.">
          There isn't enough data in this period to draw this chart. Try a wider date range.
        </EmptyState>
      )}

      {!isPending && !isError && !isEmpty && data && (
        <>
          {showTable && tableRows ? (
            <ChartTable {...tableRows(data)} />
          ) : (
            <div className="scroll-x">{children(data)}</div>
          )}

          {(data.meta.coverage_note || data.meta.estimate_reason) && (
            <footer className="mt-3 border-t border-[var(--border)] pt-3">
              {/* Never quietly average a biased subset — say what it was built from. */}
              {data.meta.coverage_note && (
                <p className="text-xs text-[var(--text-muted)]">{data.meta.coverage_note}</p>
              )}
              {data.meta.estimate_reason && (
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  {data.meta.estimate_reason}
                </p>
              )}
            </footer>
          )}
        </>
      )}
    </Card>
  );
}

export function ChartTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | number)[][];
}) {
  return (
    <div className="scroll-x max-h-80 overflow-y-auto">
      <table className="w-full text-sm tnum">
        <thead className="sticky top-0 bg-[var(--surface-1)]">
          <tr className="border-b border-[var(--border)] text-left">
            {headers.map((header) => (
              <th key={header} className="px-2 py-2 font-medium text-[var(--text-secondary)]">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            // Row content is the identity here; there is no stable id from the API.
            <tr key={index} className="border-b border-[var(--border)] last:border-0">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-2 py-1.5 text-[var(--text-primary)]">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Cards for charts the user's data can't support yet, with what would unlock them. */
export function UnlockCard({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border)] p-4">
      <p className="text-sm font-medium text-[var(--text-secondary)]">{title}</p>
      <p className="mt-1 text-xs text-[var(--text-muted)]">{hint}</p>
    </div>
  );
}
