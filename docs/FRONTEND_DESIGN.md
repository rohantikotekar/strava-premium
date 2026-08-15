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
┌──────────────────────────────────────────────────────────────┐
│  ◎ Strava Premium    Dashboard  Activities  Progress  Map  ● │  top nav, sticky
└──────────────────────────────────────────────────────────────┘

/                    Dashboard      "How am I doing?"       — the default landing
/activities          Activity list  filter, search, sort
/activities/:id      Activity detail "How did that go?"
/progress            Progress       "Am I improving?"       — PRs, curves, YoY
/map                 Heatmap        everywhere you've been
/gear                Gear           mileage + retirement
/settings            Zones, FTP, units, goals, import history, delete account
/import              Upload flow (also reachable from an in-app banner)
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
possible:

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│         See ten years of your training, properly           │
│                                                            │
│      All the analysis Strava charges for — plus a few      │
│      things it doesn't have. Your data stays yours.        │
│                                                            │
│              [ G  Continue with Google ]                   │
│                                                            │
│              ────────────  or  ────────────                │
│                                                            │
│              [ email address              ]                │
│              [ password                   ]                │
│              [        Create account      ]                │
│                                                            │
│              Already have an account? [ Log in ]           │
└────────────────────────────────────────────────────────────┘
```

Google is the primary button — one tap, no password to invent — with email+password
as the fallback, not the other way round. `[ Log in ]` swaps the form to email +
password only (Google users get a **"Continue with Google"** button there too; if
they mistakenly type a password we show *"This account uses Google sign-in"* rather
than a generic wrong-password error — see the linking rule in
[AUTH.md §3](AUTH.md#3-sign-up--log-in--google-oauth-oidc)).

Password field: a live strength hint ("12+ characters — a passphrase works great"),
never a red error until they actually submit something too weak. No confirm-password
field — it's friction that a "show password" eye icon replaces better.

Right after account creation, before anything else, comes the Strava connect screen:

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│         Connect your Strava account                         │
│                                                            │
│      This is how we pull your activities. We only ever      │
│      read — we never post or modify anything on Strava.     │
│                                                            │
│              [  Connect with Strava  ]                     │
│                                                            │
│              [ I'll do this later ]                        │
└────────────────────────────────────────────────────────────┘
```

Skippable — an account with no Strava connection yet still lands on a real (if
mostly empty) dashboard with a persistent "Connect Strava" prompt, rather than being
stuck on this screen. If they connect: after OAuth returns, we already have their
recent activities via the API. **Show a real dashboard immediately, built from
whatever the API gives us**, with a persistent banner:

```
┌────────────────────────────────────────────────────────────┐
│ ▲  You're seeing your last 30 days. Add your full history  │
│    to unlock year-over-year trends, all-time PRs, and      │
│    your fitness curve.        [ Import my history ]  [ ✕ ] │
└────────────────────────────────────────────────────────────┘
```

This matters: the user gets value **before** being asked to do the annoying part.

**The import wizard** — 3 steps, honest about the wait:

```
Step 1 ─ Request your archive from Strava
   Strava has to prepare your file. It usually takes a few
   hours, and they'll email you a download link.
   [ Open Strava's download page ↗ ]      (opens the exact settings page)
   ┌──────────────────────────────────────────────────────┐
   │ Already have the .zip? Skip to step 2.               │
   └──────────────────────────────────────────────────────┘
   [ Email me a reminder tomorrow ]

Step 2 ─ Upload it here
   ┌──────────────────────────────────────────────────────┐
   │              Drop your export.zip here               │
   │                or [ choose a file ]                  │
   │      Usually 500 MB – 10 GB. You can close this      │
   │      tab once the upload finishes.                   │
   └──────────────────────────────────────────────────────┘

Step 3 ─ We do the rest
```

Progress, driven by the SSE stream from
[INGESTION §6](INGESTION.md#6-what-the-user-sees-while-this-runs):

```
┌─────────────────────────────────────────────────────────┐
│  Uploading                        ████████░░░  1.9/2.4GB│
│  ✓ Found 3,412 activities from Mar 2015 to today        │
│  ✓ Your dashboard is ready →           [ View it now ]  │
│  ⟳ Reading detailed data      ██████░░░░░  1,204/2,987  │
│    Unlocked so far: heart-rate zones, splits, route map │
└─────────────────────────────────────────────────────────┘
```

Note "**View it now**" appears the moment the fast path lands. The user does not
wait for the deep parse.

### 2. Dashboard

```
┌───────────────────────────────────────────────────────────────────┐
│  This week                          [ Week ▾ ] [ All sports ▾ ]   │
│                                                                   │
│    42.3 km          ← hero figure, 48px                           │
│    ↑ 12% vs. your 8-week average                                  │
│                                                                   │
│  ┌──────────┬──────────┬──────────┬──────────┐                    │
│  │ Time     │ Elevation│ Activities│ Load     │   KPI row         │
│  │ 4h 12m   │ 612 m    │ 5        │ 287      │   each w/ sparkline│
│  │ ↑ 8%     │ ↓ 4%     │ —        │ ↑ 15%    │   and Δ vs. base   │
│  └──────────┴──────────┴──────────┴──────────┘                    │
├───────────────────────────────────────────────────────────────────┤
│  Form & fitness                                     [ 6 months ▾ ]│
│  You're building well — fitness is up 9 points this month and     │
│  you're fresh enough to race.                                     │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │  ╱‾‾‾╲___╱‾‾‾‾‾‾╲___  fitness (area, blue)                │    │
│  │  ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿  fatigue (line, orange)               │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  ▁▂▃▁▂▄▅▃▁ ▔▔▔▔ ▂▃▅   form (diverging bars, own panel)    │    │
│  └───────────────────────────────────────────────────────────┘    │
├───────────────────────────────────────────────────────────────────┤
│  Training calendar                                    [ 2026 ▾ ]  │
│  ▪▪▫▪▪▪▫ ▪▫▪▪▪▪▫ … 52-week heatmap, sequential blue                │
│  238 active days · longest streak 21 days                         │
├────────────────────────────────┬──────────────────────────────────┤
│  Weekly volume                 │  Recent activities               │
│  ▁▃▅▂▆▄▇▅▃  + 4-wk mean line   │  list of 5, each with sparkline   │
└────────────────────────────────┴──────────────────────────────────┘
```

Filters live in **one row at the top** and apply to the whole page. They're URL
state (`/?range=6m&sport=run`), so a view is bookmarkable and shareable.

### 3. Activity detail

```
┌───────────────────────────────────────────────────────────────────┐
│  ← Back        Morning Run · Tue 4 Aug 2026, 6:42 AM               │
│                                                     [ Strava ↗ ]  │
│  ┌────────────────────────────┬──────────────────────────────┐    │
│  │                            │  12.4 km    ← hero            │   │
│  │      route map             │  52:18 · 4:13 /km · 148 bpm   │   │
│  │      (MapLibre)            │  ↑ 212 m · Load 78            │   │
│  │                            │  🏅 Fastest 10k in 8 months   │   │
│  └────────────────────────────┴──────────────────────────────┘    │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │  pace      ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿   ← stacked panels,     │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  heart rate ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿    shared x-axis,        │    │
│  ├───────────────────────────────────────────────────────────┤    │
│  │  elevation ▁▂▄▆▇▅▃▁▂▃▅▇▆▄▂▁          one synced crosshair  │    │
│  └───────────────────────────────────────────────────────────┘    │
│      hovering the chart moves a marker along the route map        │
│                                                                   │
│  [ Splits ] [ Zones ] [ Best efforts ]     ← tabs, table content   │
└───────────────────────────────────────────────────────────────────┘
```

**Stacked panels, never overlaid dual axes.** Pace, HR, and elevation have unrelated
scales; overlaying them on two y-axes is the most common fitness-app chart mistake
and produces false correlations. One panel each, one shared time axis, one crosshair.

### 4. Progress

Tabbed: **Records** (PR table + progression) · **Curves** (power/pace curve) ·
**Zones** (distribution over time) · **Year over year**. Sport selector at the top,
because a runner and a cyclist want completely different pages here.

### 5. Settings

The screen where trust is won or lost:

- **Zones & thresholds** — max HR, resting HR, FTP. Show which are from Strava, which
  we estimated, and what changing them will recompute. Changing FTP triggers a
  visible "recomputing your history…" job, not a silent shift.
- **Units** — metric/imperial, pace vs. speed per sport.
- **Import history** — every upload, its status, the failed-file list, re-run button.
- **Account & security** — email, change password (requires current password),
  linked sign-in methods (Google — link/unlink; unlink is blocked if it's the
  *only* sign-in method and no password is set, with a clear explanation why),
  active sessions list with device/location/last-seen and a revoke button per row,
  "log out of all other devices".
- **Strava connection** — connected as `@handle`, last synced, scopes granted,
  disconnect button (disconnecting stops sync but **does not** delete already-
  imported history — that's a separate, more explicit action).
- **Your data** — export everything as CSV/Parquet. Delete account (real deletion,
  stated plainly, with what gets removed and when).

---

## <a id="chart-system"></a>Chart system

### Library split — three, deliberately

| Library | Used for | Why not one library |
|---|---|---|
| **Recharts** | All dashboard/progress charts (bars, lines, areas, stacked, heatmap cells) | React-native composition, easy to theme, fine up to ~2k points |
| **uPlot** | Activity stream panels, 5k–50k points | Recharts renders SVG nodes per point and dies here. uPlot is canvas, ~1 ms for 100k points, and it has built-in synced cursors across stacked panels — exactly our layout. |
| **MapLibre GL JS** + **deck.gl** | Route map, personal heatmap | Vector tiles, no Mapbox token, no per-load billing. deck.gl's `HeatmapLayer`/`PathLayer` handles a decade of GPS on the GPU. |

Wrapped behind `features/charts/` so a component never imports a chart library
directly — that keeps the theming, empty-state, and accessibility rules in one place
and makes a library swap a contained change.

### Design tokens

Declared once in `src/styles/viz.css` as CSS custom properties, consumed by role.
Values below are the validated reference palette (see
`references/palette.md` in the dataviz skill).

```css
.viz-root {
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
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root { /* dark steps */ }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19;  --page: #0d0d0d;
  --text-primary: #ffffff;  --text-secondary: #c3c2b7;  --text-muted: #898781;
  --gridline: #2c2c2a;  --baseline: #383835;
  --series-1: #3987e5;  --series-2: #d95926;  --series-3: #199e70;
  --series-4: #c98500;  --series-5: #d55181;  --series-6: #008300;
  --series-7: #9085e9;  --series-8: #e66767;
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
  requires: Capability[];        // e.g. ["stream.power"] — see CLAUDE.md §5
  minActivities?: number;
  minSpan?: Duration;            // e.g. "2y" for year-over-year
  unlockHint: string;            // shown when requires isn't met
  estimateNote?: string;         // shown when computed from a fallback
  Component: React.FC<ChartProps>;
}
```

`<ChartCard>` handles, uniformly: loading skeleton (chart-shaped, not a spinner),
empty state, error state with retry, the table-view toggle, the direct-label
enforcement for low-contrast slots, the download-PNG/CSV action, and the "how is this
calculated?" popover. **No chart implements those itself.**

---

## Edge cases — the ones that actually happen

Written as *condition → what the user sees*. This section is the spec; if a state
isn't here, it's a bug to be triaged into here.

### Data availability

| Condition | Behaviour |
|---|---|
| Brand-new account, 0 activities | Not an empty dashboard. A "get started" screen: connect ✓, then import history, then a sample-data preview so they can see what they'd get. |
| Fewer than 7 activities | Trend charts hidden; stat tiles show absolutes with no Δ (a Δ vs. a 2-activity baseline is noise). Message: *"A few more activities and we can start showing trends."* |
| Less than 2 years of data | Year-over-year card hidden. Not greyed out — hidden. |
| No heart-rate data ever | All HR charts absent. One card in "Unlock more": *"Wear a heart-rate monitor and we'll show training zones, TRIMP load, and aerobic decoupling."* |
| No power meter | Same, for power charts. Do **not** estimate power for runners — it's a fiction. |
| HR on some activities only | Chart renders with a coverage note: *"Based on 312 of 1,208 activities that have heart rate."* Never silently average over a biased subset. |
| Only indoor/treadmill activities | Map and heatmap nav items hidden entirely. |
| Single sport only | Sport filter hidden; sport-mix chart hidden. |
| Sport we don't have a mapping for | Appears under "Other" with its raw Strava name preserved and visible. Never dropped, never mislabelled. |
| No FTP, no max HR | Fitness curve still renders using the TRIMP/duration fallback, with a visible badge: *"Estimated — [set your zones] for accurate load."* Never silently substitute a default and present it as fact. |
| User changes FTP or max HR | Explicit confirm: *"This recomputes training load for 3,412 activities. Takes about a minute."* Then a progress toast. |

### Account & auth

| Condition | Behaviour |
|---|---|
| Signup email already registered | Response is identical to a successful signup (no enumeration); the actual account owner gets an email — *"Someone tried to sign up with your email — was that you? [Log in]"* |
| Wrong password on login | Generic *"Incorrect email or password"* — never reveal which half is wrong. After repeated failures, a progressive delay kicks in silently (no scary "account locked" message that itself confirms the email exists). |
| Signs in with Google using an email that already has a password account | Never auto-linked. *"An account already exists for this email. Log in, then connect Google from Settings."* |
| Password-only user clicks "Continue with Google" by mistake and it's a different Google identity | Normal Google-first-time flow — becomes a genuinely separate account unless they explicitly link. We don't guess. |
| Forgot password | Always responds the same regardless of whether the email exists; if it does, a reset link (1-hour expiry, single use) arrives and using it signs the user out everywhere else. |
| Strava token expired / revoked | Non-blocking amber banner (see Import & sync below) — never signs the user out of *our* account, since Strava is a connection, not identity. |
| Skipped Strava connect at signup | Dashboard renders empty-state cards with a persistent "Connect Strava" CTA, not a dead end. |
| Disconnecting Strava | Confirm dialog distinguishes "stop syncing" (keeps history) from "also delete my imported data" (the destructive one), never bundles them. |
| Session revoked remotely (another tab / device) | Next request gets a clean redirect to login with *"You were signed out — this session ended from another device."*, not a raw 401. |

### Import & sync

| Condition | Behaviour |
|---|---|
| Upload interrupted | Multipart parts survive; resume from where it stopped, don't restart. |
| Wrong file uploaded (not a Strava export) | Caught client-side before the upload, with a specific message and a link to the right page. |
| Zip has no `activities.csv` | *"We couldn't find the activity index in this archive. Is this the file Strava emailed you?"* + what a correct archive looks like. |
| 12 of 2,987 files failed to parse | Not an error state. Neutral summary + expandable list of filenames and reasons. Import is marked complete. |
| **All** files failed | This *is* an error. Explicit apology, a "send us the details" button (metadata only, never the file), and the CSV-derived dashboard still works. |
| Import running when the user returns | Banner resumes with live progress. Reloading never restarts anything. |
| Second, newer export uploaded | *"We'll merge this with what you already have. Nothing gets duplicated."* |
| Strava token expired / revoked | Non-blocking amber banner: *"Reconnect Strava to keep syncing new activities. Your existing data is safe."* Historical dashboards keep working. |
| Strava API rate-limited | Silent to the user. Sync status shows *"Syncing shortly"*, never an error. |
| Activity deleted on Strava | Removed from our views on the next webhook, with a note in import history so the count change isn't mysterious. |

### Display & interaction

| Condition | Behaviour |
|---|---|
| A single 200 km outlier squashes the chart | Never auto-clip silently. Use a robust y-domain (p99) **and** mark the clipped point with an annotation the user can click. |
| Activity with 0 distance (yoga, strength) | Duration-based tiles only. Distance shows `—`, never `0.0 km`. |
| Activity with 30,000 stream points | uPlot handles it; downsample to viewport width with min/max-preserving LTTB so spikes survive. |
| Paused / multi-segment activity | Gaps in the stream are gaps in the line, not interpolated across. Elapsed vs. moving time both shown. |
| GPS glitch (a 900 km/h spike) | Flag and exclude from max-speed stats; keep it visible in the raw trace with a marker. Never quietly delete data. |
| Timezone: activity abroad | Displayed in the activity's **local** time with the zone shown; aggregates by day use local date. |
| Very long activity names / device junk names | Truncate with a title attribute; never let it break layout. |
| Slow network | Skeletons shaped like the chart. TanStack Query keeps previous data visible while refetching, so filters never blank the page. |
| Chart data request fails | Per-card error with retry. One failed card never takes down the dashboard. |
| Printing / PDF export | Print stylesheet: light tokens, texture fills on, tables expanded. |

### Accessibility

- Keyboard: every chart is focusable, arrow keys move the crosshair, `Enter` opens
  the table view. Tab order follows visual order.
- Screen readers: each chart has an `aria-label` summarising the trend in words
  (*"Weekly distance, last 12 weeks, ranging 18 to 62 km, trending up"*) and the
  table view is the accessible equivalent.
- Respect `prefers-reduced-motion` — no chart entry animations, instant transitions.
- Target sizes ≥44px on touch. Hit areas larger than the marks they select.
- Never color-only: legend + direct labels + optional texture.

---

## Copy guidelines

The voice is **a knowledgeable training partner, not a coach and not a marketer.**

| Do | Don't |
|---|---|
| "Your fitness is up 9 points this month." | "Amazing work, superstar! 🔥🔥" |
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
</content>
</invoke>
