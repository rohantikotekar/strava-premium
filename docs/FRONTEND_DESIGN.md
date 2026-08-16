# Frontend design

## Design principle

**The dashboard is a training log, not a control panel.**

Fitness enthusiasts open this app for one of three reasons: *"how am I doing?"*,
*"how did that session go?"*, or *"am I improving?"*. Every screen answers one of
those in its first 200 pixels. Everything else is progressive disclosure.

Four rules that resolve most design arguments:

1. **The answer comes before the chart.** Every card leads with a sentence in plain
   language — *"You're 12% ahead of last year"* — and the chart is the evidence.
2. **Never show an empty chart.** A chart with no data is not rendered. Its slot
   either disappears or becomes a card explaining what would unlock it.
3. **Nothing spins with no explanation.** Every loading state says what is loading
   and roughly how long it takes.
4. **Never invent numbers.** A metric derived from an estimate (no FTP, no max HR)
   is labelled as an estimate at the point of display, not in a footnote.

---

## Information architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ ● Training Stats  Dashboard Activities Progress Import Settings   │
│                                        you@x.com  [☀/☾]  Sign out │  top nav, sticky
└──────────────────────────────────────────────────────────────────┘

/                    Dashboard      "How am I doing?"       — the default landing
/activities          Activity list  filter, search, sort
/activities/:id      Activity detail "How did that go?"
/progress            Progress       "Am I improving?"       — PRs, curves, YoY
/import              Upload flow (also reachable from an in-app banner)
/settings            Zones, FTP, units, import history, delete account
```

Five nav items. Not eleven. If something doesn't fit, it belongs *inside* one of
these, not beside them.

Mobile: the same five as a bottom tab bar. Charts stack single-column; the activity
stream chart becomes horizontally pannable rather than shrinking to illegibility.

---

## Screen-by-screen

### 1. First run — the onboarding that decides everything

This flow is the product's biggest risk. A user who lands on an empty dashboard
churns instantly.

Account creation is deliberately **separate from Strava** (full rationale and flows
in [AUTH.md](AUTH.md)) — signup can never be blocked by Strava's new-app athlete cap,
and the landing page's job is just to get an account created with the least friction
possible.

```
┌────────────────────────────────────────────────────────────┐
│                                              [☀/☾]          │
│         See ten years of your training, properly            │
│                                                              │
│      All the analysis Strava charges for — plus a few       │
│      things it doesn't have. Your data stays yours.         │
│                                                              │
│              [ G  Continue with Google ]                    │
│              ────────────  or  ────────────                 │
│              [ email address              ]                 │
│              [ password                   ]                 │
│              [        Create account      ]                 │
│                                                              │
│              Already have an account? [ Log in ]            │
└────────────────────────────────────────────────────────────┘
```

Google is the primary button — one tap, no password to invent — with email+password
as the fallback, not the other way round. Password field: a live strength hint
("12+ characters — a passphrase works great"), never a red error until they've
actually submitted something too weak. No confirm-password field — a "show
password" toggle beats it.

This is also the one screen in the app that carries a deliberate branded touch: a
quiet accent-tinted radial glow behind the card (`.accent-glow` in `index.css`).
Every other screen is flat — the glow is reserved for this single "welcome" moment
so it never competes with a chart.

Right after account creation comes the Strava connect screen — skippable, and an
account with no connection yet still lands on a real dashboard with a persistent
"Connect Strava" prompt rather than being stuck here. If they connect: after OAuth
returns, we already have their recent activities via the API, so a real dashboard
renders immediately, with a banner offering the full-history import.

**The import wizard** — 3 steps, honest about the wait:

```
Step 1 ─ Request your archive from Strava
   [ Open Strava's download page ↗ ]   [ Email me a reminder tomorrow ]

Step 2 ─ Upload it here
   ┌──────────────────────────────────────────────────────┐
   │        Drop your export.zip here or [ choose a file ] │
   └──────────────────────────────────────────────────────┘

Step 3 ─ We do the rest
   Uploading                        ████████░░░  1.9/2.4GB
   ✓ Found 3,412 activities from Mar 2015 to today
   ✓ Your dashboard is ready →           [ View it now ]
   ⟳ Reading detailed data      ██████░░░░░  1,204/2,987
     Unlocked so far: heart-rate zones, splits, route map
```

"**View it now**" appears the moment the fast path lands — the user does not wait
for the deep parse.

### 2. Dashboard

```
┌───────────────────────────────────────────────────────────────────┐
│  This week                          [ Week ▾ ] [ All sports ▾ ]   │
│                                                                    │
│    42.3 km          ← hero figure, text-6xl/bold                  │
│    ↑ 12% vs. your 8-week average                                  │
│                                                                    │
│  ┌──────────┬──────────┬──────────┬──────────┐                    │
│  │ Time     │ Elevation│ Activities│ Load ⓘ  │   KPI row          │
│  │ 4h 12m   │ 612 m    │ 5        │ 287      │                    │
│  └──────────┴──────────┴──────────┴──────────┘                    │
│  [25 active days] [Current streak 4d] [Longest streak 21d]        │
├────────────────────────────────────────────────────────────────────┤
│  Fitness & freshness ⓘ                              [Estimated]   │
│  Am I building fitness, and am I fresh?                           │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │  ╱‾‾‾╲___╱‾‾‾‾‾‾╲___  fitness (area, series-1)             │    │
│  │  ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿  fatigue (line, series-2)              │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  ▁▂▃▁▂▄▅▃▁ ▔▔▔▔ ▂▃▅   form (diverging bars, own panel)     │    │
│  └───────────────────────────────────────────────────────────┘    │
├────────────────────────────────────────────────────────────────────┤
│  Training calendar                                     [2026 ▾]   │
├────────────────────────────┬─────────────────────────────────────┤
│  Weekly volume              │  Sport mix                          │
└────────────────────────────┴─────────────────────────────────────┘
```

The "ⓘ" markers are `InfoDot` — a single "?" affordance even when it explains
several related terms at once. "Fitness & freshness" covers CTL, ATL and TSB;
three separate icons next to one title reads as clutter, so `InfoDot` accepts
either one term or a list and renders **one** icon with everything stacked in the
popover. Reach for the list form whenever a chart's title already names the
cluster; a chart explaining one idea still gets one term, one icon.

Filters live in **one row at the top** and apply to the whole page, as URL/component
state — a view stays consistent as you navigate.

### 3. Activity detail

```
┌───────────────────────────────────────────────────────────────────┐
│  ← Back        Morning Run · Tue 4 Aug 2026, 6:42 AM               │
│  12.4 km · 52:18 · 4:13/km · 148 bpm · ↑212m · Load 78            │
│                                                                    │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │  pace      ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿   ← stacked panels,       │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  heart rate ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿    shared x-axis,          │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  elevation ▁▂▄▆▇▅▃▁▂▃▅▇▆▄▂▁          one synced crosshair   │    │
│  └───────────────────────────────────────────────────────────┘    │
│  [ Splits ] [ Zones ] [ Best efforts ]     ← tabs, table content   │
└───────────────────────────────────────────────────────────────────┘
```

**Stacked panels, never overlaid dual axes.** Pace, HR, and elevation have unrelated
scales; overlaying them on two y-axes is the most common fitness-app chart mistake
and produces false correlations. One panel each, one shared time axis.

### 4. Progress

Tabbed by content, not by sport: **Records** (PR table + progression) · **Curves**
(power/pace curve) · **Zones** (distribution over time) · **Year over year**. A
sport selector at the top changes what all of them show — a runner and a cyclist
want completely different pages here.

### 5. Settings

The screen where trust is won or lost:

- **Zones & thresholds** — max HR, resting HR, FTP, each with a plain-language hint
  ("Highest you've actually seen", "Cycling only — roughly what you could hold for
  an hour"). Changing one triggers a visible "recomputing your history…" job, not a
  silent shift in every chart's numbers.
- **Units** — metric/imperial.
- **Import history** — every upload, its status, the failed-file list.
- **Your data** — export everything as CSV/Parquet. Delete account (real deletion,
  stated plainly, with what gets removed and when).

---

## <a id="chart-system"></a>Chart system

### Library split — three, deliberately

| Library | Used for | Why not one library |
|---|---|---|
| **Recharts** | All dashboard/progress charts (bars, lines, areas, stacked) | React-native composition, easy to theme, fine up to ~2k points |
| **uPlot** | Activity stream panels, 5k–50k points | Recharts renders SVG nodes per point and dies here. uPlot is canvas, ~1 ms for 100k points, and has built-in synced cursors across stacked panels — exactly our layout. |
| **MapLibre GL JS** + **deck.gl** | Route map, personal heatmap | Vector tiles, no Mapbox token, no per-load billing. |

Wrapped behind `features/charts/` so a component never imports a chart library
directly — that keeps theming, empty-state, and accessibility rules in one place.

### Design tokens

Declared once in `apps/web/src/index.css` as CSS custom properties, consumed by
role. The categorical/sequential/status values are the validated reference palette
(see `references/palette.md` in the dataviz skill) and are **chart-only** — never
repurposed for buttons, nav, or badges (rule 9a below).

```css
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;  --page: #f9f9f7;
  --text-primary: #0b0b0b;  --text-secondary: #52514e;  --text-muted: #898781;
  --gridline: #e1e0d9;  --baseline: #c3c2b7;
  --series-1: #2a78d6;  --series-2: #eb6834;  --series-3: #1baf7a;
  --series-4: #eda100;  --series-5: #e87ba4;  --series-6: #008300;
  --series-7: #4a3aa7;  --series-8: #e34948;
  --seq-100: #cde2fb; --seq-250: #86b6ef; --seq-400: #3987e5;
  --seq-550: #1c5cab; --seq-700: #0d366b;
  --status-good: #0ca30c; --status-warning: #fab219;
  --status-serious: #ec835a; --status-critical: #d03b3b;
  --de-emphasis: #c3c2b7;

  /* Brand accent — UI chrome only: primary buttons, the active nav
     underline, streak/achievement badges, and the login page's glow.
     Deliberately a different hue from every --series-N (and from Strava's
     own #FC4C02 brand orange) so an accented button never reads as chart
     data, and this unaffiliated product never visually implies endorsement. */
  --accent: #cc4e10;      /* 4.5:1 on white */
  --accent-ink: #ffffff;
  --accent-wash: rgb(204 78 16 / 0.1);

  /* Card elevation. Light mode reads depth from a real drop shadow. */
  --card-shadow: 0 1px 2px rgb(11 11 11 / 0.05), 0 1px 1px rgb(11 11 11 / 0.04);
  --card-shadow-hover: 0 8px 20px rgb(11 11 11 / 0.08), 0 2px 6px rgb(11 11 11 / 0.05);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) { /* dark steps, below */ }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19;  --page: #050505;
  --text-primary: #ffffff;  --text-secondary: #c3c2b7;  --text-muted: #898781;
  --gridline: #2c2c2a;  --baseline: #383835;
  --series-1: #3987e5;  --series-2: #d95926;  --series-3: #199e70;
  --series-4: #c98500;  --series-5: #d55181;  --series-6: #008300;
  --series-7: #9085e9;  --series-8: #e66767;

  --accent: #f0793d;      /* 6.2:1 on the dark surface */
  --accent-ink: #1a1a19;
  --accent-wash: rgb(240 121 61 / 0.16);

  /* Dark mode CANNOT read depth from a shadow — black-on-near-black is
     invisible — so it reads depth from a soft ambient shadow plus a 1px
     "rim light" top edge instead (an inset highlight, the standard dark-UI
     substitute for shadow). Note --page is darkened to #050505 rather than
     --surface-1 lightened, specifically so cards separate from the page
     without touching the surface color the chart-contrast checks above were
     validated against. */
  --card-shadow: inset 0 1px 0 0 rgb(255 255 255 / 0.05), 0 2px 8px rgb(0 0 0 / 0.5);
  --card-shadow-hover: inset 0 1px 0 0 rgb(255 255 255 / 0.08), 0 8px 24px rgb(0 0 0 / 0.6);
}
```

Dark mode is a **selected** set of steps for the dark surface, not an inverted flip.
Both modes were run through the palette validator:

```
light: lightness PASS · chroma PASS · CVD PASS (worst adjacent ΔE 9.1)
       normal-vision PASS (19.6) · contrast WARN → 3 slots below 3:1
dark:  all six checks PASS (CVD 8.4, normal-vision 19.3, contrast ≥3:1)
```

The light-mode WARN is **not dismissable**: aqua (`--series-3`), yellow
(`--series-4`), and magenta (`--series-5`) sit below 3:1 on the light surface, so any
chart using them ships **visible direct labels or a table view**. This is enforced in
the chart wrapper, not left to the author.

### Typography scale

A deliberate, small site-wide bump, not Tailwind defaults left alone:

```css
html { font-size: 106%; }   /* every rem-based Tailwind size lifts a touch */
```

On top of that, specific elements take an explicit larger step so the hierarchy
reads clearly at a glance:

| Element | Class |
|---|---|
| Hero figure (dashboard, activity detail) | `text-6xl font-bold tracking-tight` |
| Page title (`<h1>`: Activities, Progress, Settings…) | `text-3xl font-bold tracking-tight` |
| Card / chart section header (`<h2>`) | `text-base font-semibold` |
| KPI tile value | `text-2xl font-bold` |
| Body copy, table cells | `text-sm` / `text-[0.95rem]` (buttons) |

Everything — including the hero figure — stays in the system sans
(`system-ui, -apple-system, "Segoe UI", sans-serif`). No display or serif face
anywhere, and no second (monospace) family either: `.tnum`
(`font-variant-numeric: tabular-nums`) gets column alignment for tables and axis
ticks without loading a font.

### Light/dark switching

`lib/theme.ts` + the `ThemeToggle` component (a sun/moon icon button, top-right of
the nav and of the login page). Three states: `light` / `dark` / `system` — the
default is `system`, which sets no `data-theme` attribute at all and simply follows
`prefers-color-scheme`. Choosing an explicit mode pins `data-theme` and persists it
to `localStorage`; an inline script in `index.html` applies the stored value
*before paint*, so an explicit choice never flashes the wrong theme on reload.

### Binding chart rules

These are not style preferences; violations are review-blocking.

1. **Never a dual y-axis.** Two measures of different scale become two stacked panels
   sharing an x-axis, small multiples, or both indexed to a common base. This is the
   #1 chart error and fitness dashboards are full of it.
2. **Color follows the entity, never the rank.** `sport_group` → fixed slot map, held
   in one constant. Filtering out "Ride" must not repaint "Run".
3. **Assign categorical slots in order 1→8, never cycled.** A 9th series folds into
   "Other" or the chart becomes small multiples.
4. **Series-count ladder:** 1–3 comfortable; at 4, direct labels become mandatory
   (yellow and orange are now both on screen). Scatter/bubble/small-multiple forms
   cap at **3 series** — the all-pairs gate fails past that.
5. **Sequential = one hue, light→dark** (zones, heatmaps, calendar). **Diverging =
   blue↔red with a gray midpoint** (form/TSB, vs-target, grade). Never a rainbow;
   never a hue at a diverging midpoint.
6. **Zones use the ordinal ramp, not categorical hues.** HR zones are *ordered*;
   painting them 5 different colors destroys that. Start no lighter than `--seq-250`
   on light so zone 1 still clears 2:1.
7. **Emphasis over categorical when one series is the point.** Year-over-year is one
   blue line + gray context lines, not ten hues.
8. **Legend present for ≥2 series** (a single series is named by the title); ≤4 series
   are also direct-labelled. Identity is never carried by color alone.
9. **Status colors are reserved** for good/warning/serious/critical and always ship
   with an icon + label. `--status-critical` is never "series 8".
9a. **`--accent` never appears inside a chart mark.** It's UI chrome (buttons, the
   active nav underline, streak badges, the login glow) — the whole reason it's a
   distinct hue from every `--series-N` (and from Strava's own brand orange) is so
   an accented button can sit next to an orange "Ride" segment without the two
   looking like the same entity, and so the app never visually implies affiliation.
10. **Marks:** 2px lines, ≥8px hover targets, 4px rounded bar ends at the baseline,
    2px surface gap between stacked segments and adjacent bars, 2px surface ring where
    marks overlap. Grid and axes recessive (`--gridline`, `--text-muted`).
11. **Text wears text tokens**, never a series color. A colored swatch beside the
    label carries identity.
12. **Every chart has a hover layer by default** — crosshair + tooltip on
    line/area, per-mark tooltip on bar/dot/cell. The only exception is a bare stat
    tile with no plot.
13. **Every chart has a table view** behind a toggle. It's the accessibility
    fallback, the contrast-WARN relief, and honestly it's what power users want anyway.
14. **Texture fill** (45°/135° hand-drawn lines) ships behind the accessibility
    setting, print, and `forced-colors`. Never decorative, never on by default.

### The chart wrapper contract

Every chart is registered, and the registry is what enforces the above:

```ts
interface ChartDef {
  id: string;
  title: string;
  question: string;              // the plain-language question it answers
  requires: string[];             // capabilities, e.g. ["stream.power"] — CLAUDE.md §5
  minCoverage?: number;
  minSpanDays?: number;
  unlockHint: string;             // shown when requires isn't met
  section: "dashboard" | "progress";
  span?: 1 | 2;
}
```

`<ChartCard>` handles, uniformly: loading skeleton (chart-shaped, not a spinner),
empty state, error state with retry, the table-view toggle, the direct-label
enforcement for low-contrast slots, and the optional glossary `InfoDot` next to the
title. **No chart implements those itself.**

---

## Edge cases — the ones that actually happen

Written as *condition → what the user sees*. This section is the spec; if a state
isn't here, it's a bug to be triaged into here.

### Data availability

| Condition | Behaviour |
|---|---|
| Brand-new account, 0 activities | Not an empty dashboard. A "get started" screen: connect ✓, then import history. |
| Fewer than 7 activities | Trend charts hidden; stat tiles show absolutes with no Δ. Message: *"A few more activities and we can start showing trends."* |
| Less than 2 years of data | Year-over-year card hidden. Not greyed out — hidden. |
| No heart-rate data ever | All HR charts absent. One card in "Unlock more" explains what device/data would enable them. |
| No power meter | Same, for power charts. Do **not** estimate power for runners — it's a fiction. |
| HR on some activities only | Chart renders with a coverage note: *"Based on 312 of 1,208 activities that have heart rate."* Never silently average over a biased subset. |
| Only indoor/treadmill activities | Map and heatmap nav entries hidden entirely. |
| Single sport only | Sport filter hidden; sport-mix chart hidden. |
| Sport we don't have a mapping for | Appears under "Other" with its raw Strava name preserved and visible. Never dropped, never mislabelled. |
| No FTP, no max HR | Fitness curve still renders using the TRIMP/duration fallback, with a visible `Estimated` badge. Never silently substitute a default and present it as fact. |
| User changes FTP or max HR | Explicit confirm before recomputing training load for every affected activity; a progress toast while it runs. |

### Import & sync

| Condition | Behaviour |
|---|---|
| Upload interrupted | Multipart parts survive; resume from where it stopped, don't restart. |
| Wrong file uploaded (not a Strava export) | Caught before or right after upload, with a specific message pointing at the right page. |
| Zip has no `activities.csv` | *"We couldn't find the activity index in this archive. Is this the file Strava emailed you?"* |
| Some files failed to parse | Not an error state. Neutral summary + expandable list of filenames and reasons. Import is marked complete. |
| **All** files failed | This *is* an error, with a way to see details. The CSV-derived dashboard, if any landed, still works. |
| Import running when the user returns | Progress resumes from the server; reloading never restarts anything. |
| Second, newer export uploaded | Merges with existing history — nothing gets duplicated (natural-key dedupe). |
| Strava token expired / revoked | Non-blocking banner: reconnect to keep syncing; existing data stays intact. |
| Activity deleted on Strava | Removed from our views on the next sync, noted in import history. |

### Account & auth

| Condition | Behaviour |
|---|---|
| Email already registered at signup | Identical response either way (AUTH.md §5) — the API never confirms or denies an address exists. |
| Wrong password | Generic *"That email or password isn't right"*, with progressive rate limiting behind the scenes. |
| Google sign-in, email matches an existing password account | Never auto-linked. Banner: *"An account already exists for that email. Log in, then link Google from Settings."* |
| Disconnecting Strava | Removes the data connection only — the account, login, and already-imported history are untouched. |

### Display & interaction

| Condition | Behaviour |
|---|---|
| A single 200 km outlier squashes the chart | Never auto-clip silently. Robust y-domain (p99) **and** the clipped point stays visible/interactive. |
| Activity with 0 distance (yoga, strength) | Duration-based tiles only. Distance shows `—`, never `0.0 km`. |
| Activity with 30,000 stream points | uPlot handles it; downsample to viewport width with min/max-preserving sampling so spikes survive. |
| Paused / multi-segment activity | Gaps in the stream are gaps in the line, not interpolated across. |
| GPS glitch (a 900 km/h spike) | Flagged and excluded from max-speed stats; stays visible in the raw trace. |
| Activity abroad | Displayed in the activity's **local** time with the zone shown. |
| Slow network | Skeletons shaped like the chart; previous data stays visible while refetching. |
| Chart data request fails | Per-card error with retry. One failed card never takes down the dashboard. |

### Accessibility

- Keyboard: every chart is focusable, arrow keys move the crosshair, `Enter` opens
  the table view. Tab order follows visual order.
- Screen readers: each chart has an `aria-label` summarising the trend in words; the
  table view is the accessible equivalent.
- Respect `prefers-reduced-motion` — no chart entry animations, instant transitions.
- Target sizes ≥44px on touch. Hit areas larger than the marks they select.
- Never color-only: legend + direct labels + optional texture.

---

## Copy guidelines

The voice is **a knowledgeable training partner, not a coach and not a marketer.**

| Do | Don't |
|---|---|
| "Your fitness is up 9 points this month." | "Amazing work, superstar!" |
| "Based on 312 activities with heart rate." | (silence about the subset) |
| "Estimated — set your max HR for accuracy." | (presenting an estimate as measured) |
| "12 files couldn't be read. See which ones." | "Import failed." |
| "This usually takes a few minutes." | "Please wait…" |
| "Fitness (CTL) is your 42-day average training load." | assuming everyone knows CTL |

Every jargon term (CTL, ATL, TSB, TSS, TRIMP, NP, GAP, ACWR, decoupling) has a
`?` popover with a one-sentence definition and a one-sentence "why you'd care".
Written once in `lib/glossary.ts`, referenced everywhere.

No streaks-guilt, no notifications about missed workouts, no comparisons to other
users. This app is a mirror, not a nag.
