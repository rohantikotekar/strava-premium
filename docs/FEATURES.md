# Features — what we compute and what we draw

Every feature below is written as **data required → computation → chart form**.
The "Requires" column is the literal capability string checked against
`user_capabilities`; if a user doesn't have it, the chart is not rendered (see
[CLAUDE.md §5](../CLAUDE.md#5-the-capability-model-read-this-twice)).

Chart forms follow the rules in [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md#chart-system).

---

## Availability tiers

| Tier | Meaning |
|---|---|
| **P** | Behind Strava's paid subscription today — this is our core value proposition |
| **F** | Free on Strava, but we do it better (longer history, better interaction, cross-sport) |
| **N** | Strava doesn't offer it at all |

---

## v1 — the shippable core

Everything here works from `activities.csv` alone (fast path) except where a stream
capability is noted. That's deliberate: **v1's dashboard must be fully useful before
a single FIT file is parsed.**

### Overview dashboard

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 1 | **Headline stats** — lifetime distance, time, elevation, activity count | F | — | Sum over all activities | **KPI row** of stat tiles, each with a 12-month sparkline. Lifetime distance is the **hero figure**. |
| 2 | **Training calendar heatmap** | P | — | Daily sum of `training_load` (or duration when no load) | GitHub-style **year heatmap**, sequential blue ramp, one row per weekday. Year selector. |
| 3 | **Weekly volume trend** | F | — | `mv_weekly_volume`, rolling 4-week mean overlaid | **Column chart** + a 4-week rolling mean line, same hue, line darker. Not two axes — both are distance. |
| 4 | **Year-over-year cumulative distance** | P | ≥2 yrs | Cumulative distance by day-of-year, one line per year | **Multi-line**, current year in slot 1, prior years in de-emphasis gray → **emphasis form**, not 10 categorical hues. |
| 5 | **Sport mix over time** | F | ≥2 sports | Monthly time by `sport_group` | **Stacked column**, categorical slots in fixed order, 2px surface gap between segments. Caps at 7 sports + "Other". |
| 6 | **Consistency & streaks** | N | — | Longest streak, current streak, active days/week, biggest gap | **Stat tiles** + a small **dot strip** of the last 90 days |

### Fitness & load

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 7 | **Fitness / Fatigue / Form (CTL-ATL-TSB)** | P | — | `training_load` per day → CTL = 42-day EWMA, ATL = 7-day EWMA, TSB = CTL − ATL. Load source: TSS if power, else TRIMP if HR, else RPE×duration, else duration-based estimate. Rest days count as 0. | **Layered chart**: CTL area (blue), ATL line (orange), TSB as a **diverging bar** on a *separate stacked panel below, sharing the x-axis*. Never a second y-axis. |
| 8 | **Relative Effort trend** | P | — | Weekly sum of `training_load`, with a personal 6-week baseline band | **Column chart** + baseline band; columns above/below band use the diverging pair |
| 9 | **Training load composition** | N | `stream.heartrate` | % of weekly load in each HR zone | **Stacked horizontal bar** per week, sequential-ordinal blue ramp (zone 1 lightest → zone 5 darkest). Ordinal ramp, not categorical — zones are ordered. |
| 10 | **Acute:Chronic Workload Ratio** | N | — | ATL/CTL, with the 0.8–1.3 "safe" band | **Line vs. baseline band**; excursions marked with status colors + an icon and label, never color alone |

### Performance

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 11 | **Personal records board** | P | — | Best time per distance from `activity_distance_prs` | **Table** (7+ rows of ordered facts — a table beats a chart here) with a mini progression sparkline per row |
| 12 | **PR progression** | P | ≥3 efforts | Best-ever time at a distance, over time | **Step line**, one distance at a time via a selector — not 7 lines at once |
| 13 | **Pace / speed distribution** | P | — | Histogram of activity average pace, faceted by year | **Small multiples** of histograms, one hue |
| 14 | **Power curve** (mean-maximal power) | P | `stream.power` | Best sustained W for each duration from `activity_best_efforts`, all-time vs. last 90 days | **Log-x line chart**, 2 series: all-time (gray, context) + selected period (blue, emphasis) |
| 15 | **Pace curve** | N | `stream.distance` | Same construction as #14 but for pace — the running equivalent, which Strava doesn't offer | **Log-x line chart** |
| 16 | **Heart-rate zone distribution** | P | `stream.heartrate` | `activity_zone_time` summed per period | **Stacked horizontal bar** (ordinal ramp) + a per-zone time table |
| 17 | **Estimated race times** | N | ≥1 PR | Riegel: `T₂ = T₁ × (D₂/D₁)^1.06`, from best recent effort; show the input effort and date | **Table** with confidence note. Explicitly labelled an estimate. |

### Activity detail

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 18 | **Route map** | F | `stream.latlng` | Decoded polyline | **MapLibre** map, route in slot-1 blue, start/end markers |
| 19 | **Stream chart** — pace/HR/power/elevation/cadence | P (HR/power analysis) | `stream.*` | Read one Parquet | **uPlot stacked panels** sharing an x-axis, one metric per panel. Synchronised crosshair. Never overlaid on multiple y-axes. |
| 20 | **Elevation profile with gradient shading** | P | `stream.altitude` | Grade per segment | **Area chart** with a diverging fill by grade sign |
| 21 | **Splits table** | F | `stream.distance` | `activity_splits` | **Table** with an inline bar in the pace column |
| 22 | **Best efforts within this activity** | P | streams | `activity_best_efforts` for this activity, flagged if all-time | **Table** + badge |
| 23 | **Zone breakdown for this activity** | P | `stream.heartrate` | `activity_zone_time` | **Stacked bar**, ordinal ramp |

### Gear & goals

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 24 | **Gear mileage + retirement alerts** | F (alerts: N) | `field.gear` | Distance summed per gear; alert at user threshold | **Horizontal bar** with a target marker; status icon + label when over |
| 25 | **Goal tracking** (distance/time/elevation, week/month/year) | P | — | Progress vs. target, with a pace-to-goal projection | **Meter** per goal + a projection line on the cumulative chart |

### Maps

| # | Feature | Tier | Requires | Computation | Form |
|---|---|---|---|---|---|
| 26 | **Personal heatmap** — everywhere you've ever been | P | `stream.latlng` | All polylines, decoded + rendered client-side | **deck.gl heatmap layer** on MapLibre. Sequential ramp. Filter by sport/year. |

---

## v2 — depth

| Feature | Tier | Notes |
|---|---|---|
| **Matched runs/rides** — same route, compared over time | P | Route similarity via geohash-prefix bucketing + Fréchet distance on simplified polylines. Then: progression on *your* routes, controlling for terrain. |
| **Segment history** | P | From `segments/` in the export (not the API — see [STRAVA_API §5](STRAVA_API.md#5-endpoints-we-use)). Personal leaderboard over 10 years. |
| **Weather correlation** | N | Backfill historical weather per activity from **Open-Meteo's free historical API** (lat/lng + time). Then: "your pace drops 4 s/km per 5 °C above 18 °C." Genuinely novel and cheap to build. |
| **Time-of-day / day-of-week performance** | N | Needs `start_time_local` — which is exactly why we store it separately. Polar/heatmap of when you train vs. how well. |
| **Aerobic decoupling & efficiency factor trend** | N | `decoupling_pct` per activity → trend. The classic aerobic-base indicator; no consumer app shows it well. |
| **Negative-split analysis** | N | From splits — what % of your runs are negative-split, and does it correlate with PRs? |
| **Cadence / stride-length analysis** | P | Distribution + trend, with a pace-vs-cadence scatter (≤3 series under the all-pairs cap). |
| **Elevation & climbing analytics** | P | VAM, total vertical per period, biggest climbs table |
| **Multi-year goals + projections** | P | |
| **Data export** (CSV/Parquet of everything we derived) | N | Users own their data; this is also a trust signal |
| **Public shareable year-in-review page** | N | Opt-in, single-page, no raw GPS |

## v3 — differentiators

| Feature | Notes |
|---|---|
| **Training plan adherence** | Import a plan; compare planned vs. actual load |
| **Injury-risk flags** | ACWR + sudden volume ramp + load monotony; framed as information, never medical advice |
| **Multi-source ingest** | Garmin Connect, Wahoo, Polar, Apple Health exports — the canonical-schema design means one adapter each |
| **Cross-training impact** | Does strength work correlate with running durability? |
| **Natural-language querying** | "Show me my fastest 10ks in cold weather." Requires an explicit user opt-in and a compliance review — see [STRAVA_API §6](STRAVA_API.md#compliance). |

---

## Metric formulas (the ones that must be right)

Implemented in `packages/core/src/sp_core/metrics/`, each pure and unit-tested
against hand-computed values.

```
TRIMP (Banister)      = duration_min × HRr × 0.64 × e^(k × HRr)
                        HRr = (HRavg − HRrest) / (HRmax − HRrest)
                        k = 1.92 (male) / 1.67 (female); default 1.92 with a note

Normalized Power      = ⁴√( mean( rolling_30s_mean(power)⁴ ) )
Intensity Factor      = NP / FTP
TSS                   = (duration_s × NP × IF) / (FTP × 3600) × 100

CTL (fitness)         = EWMA(daily_load, τ=42d)   → α = 1 − e^(−1/42)
ATL (fatigue)         = EWMA(daily_load, τ=7d)    → α = 1 − e^(−1/7)
TSB (form)            = CTL_yesterday − ATL_yesterday

Grade Adjusted Pace   = pace × f(grade), f from Minetti et al. energy-cost polynomial
Aerobic decoupling    = (EF_first_half − EF_second_half) / EF_first_half × 100
                        EF = normalized_power / avg_HR  (or speed / avg_HR for runs)
ACWR                  = ATL / CTL
Riegel prediction     = T₂ = T₁ × (D₂ / D₁)^1.06
Mean-maximal curve    = for each duration d: max over all windows of length d
                        (computed with a sliding-window max on the cumulative sum —
                         O(n) per duration, not O(n·d))
```

**Fallback ladder for `training_load`**, in order — always record which was used, and
show it in the UI, because comparing a TSS-derived CTL to a duration-derived one is
meaningless:

1. `TSS` — if power + FTP available
2. `TRIMP` — if HR + max/rest HR available
3. `RPE load` = `perceived_exertion × duration_min`
4. `duration_min × sport_intensity_factor` — crude, clearly labelled as an estimate

---

## What we deliberately do **not** build

- **Social features** — kudos, comments, following. Strava owns that; the API terms
  restrict showing other athletes' data anyway.
- **Live activity recording.** We are an analysis layer, not a tracker.
- **Global segment leaderboards.** Rate-limit expensive and restricted.
- **Anything that requires a `write` scope.** Not needed, and it makes the OAuth
  consent screen scary.
</content>
</invoke>



