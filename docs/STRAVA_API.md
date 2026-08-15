# Strava API integration

> **Verify before launch.** Strava has changed rate limits, scopes, and API terms
> several times (notably a restrictive API Agreement revision in Nov 2024). Treat
> every number in this doc as a *default*, read the live values from response
> headers, and re-read <https://developers.strava.com> before going to production.

The design principle: **the API is for keeping up, not catching up.** Ten years of
history comes from the user's bulk export. The API only ever handles the trickle of
new activities. This is what keeps us inside the rate limit as user count grows.

---

## 1. App registration

Register at <https://www.strava.com/settings/api>. You get `client_id`,
`client_secret`, and set an Authorization Callback Domain.

Strava applies a **default cap on how many athletes can connect to a new app**
(historically 1 athlete for an unverified app, and a low ceiling until you request an
increase). Requesting a raised limit requires a working app and a description of use.
**Start this request early** — it has a human review turnaround and it will otherwise
be the thing that blocks launch.

---

## 2. OAuth 2.0 — connecting Strava (not logging in)

> Account creation and login are email+password or Google, and are **not** part of
> this flow — see [AUTH.md](AUTH.md). This section runs for an already-authenticated
> user who is connecting Strava as a data source, typically from the import wizard
> in [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md#1-first-run--the-onboarding-that-decides-everything).
> Decoupling the two means Strava's athlete-connection cap (§1) can never block
> signup, and a revoked/expired Strava token never locks a user out of their own
> account or already-imported history.

```
GET https://www.strava.com/oauth/authorize
    ?client_id={id}
    &redirect_uri={api}/connections/strava/callback
    &response_type=code
    &approval_prompt=auto
    &scope=read,activity:read_all,profile:read_all
    &state={csrf_token_bound_to_current_session}
```

Scopes we request and why:

| Scope | Why |
|---|---|
| `read` | Public profile, basic athlete record |
| `activity:read_all` | **Required** — without `_all` we silently miss every private activity, which for many users is most of them |
| `profile:read_all` | FTP, weight, HR zones, gear list — inputs to the fitness model |

We deliberately do **not** request any `write` scope. It's not needed and it makes
the consent screen scarier.

Exchange and refresh:

```
POST https://www.strava.com/oauth/token
  grant_type=authorization_code  &  code={code}      → access + refresh token
  grant_type=refresh_token       &  refresh_token=…  → new pair
```

- Access tokens are short-lived (**~6 hours**); `expires_at` is a unix timestamp in
  the response.
- **Refresh tokens can rotate** — the refresh response may contain a *new*
  `refresh_token`. Always persist whatever comes back. Not doing this is the classic
  bug that logs everyone out a week after launch.
- Refresh proactively at `expires_at - 300s`, guarded by a Redis lock per user so two
  concurrent tasks don't race and invalidate each other's token.
- Tokens are encrypted at rest and never appear in a response model or a log line.

This flow uses the app session cookie the user already has from [AUTH.md](AUTH.md) —
it does not create or replace a session. Tokens returned by the exchange are
encrypted and written to `strava_connections` (schema in
[AUTH.md §1](AUTH.md#1-identity-model)) and never reach the browser.

---

## 3. <a id="rate-limits"></a>Rate limits — the real scaling constraint

Limits are **per application, not per user.** Every user shares one budget. This is
the single most important fact about the integration.

Strava enforces two windows (a 15-minute and a daily one) and, since 2024, tracks
read-only requests separately. Commonly seen defaults:

| | 15-minute | Daily |
|---|---|---|
| Overall | 100–200 requests | 1,000–2,000 requests |
| Read-only | 100 | 1,000 |

Newer apps get the lower tier; limits can be raised on request. **Do not hardcode
these.** Every response carries them:

```
X-RateLimit-Limit:      200,2000        # 15min,daily
X-RateLimit-Usage:      45,312
X-ReadRateLimit-Limit:  100,1000
X-ReadRateLimit-Usage:  30,190
```

### Implementation

A **Redis-backed token bucket shared by all sync workers**, updated from those
headers after every response — the headers are the source of truth, our counter is
just a predictive gate.

```python
# packages/core/src/sp_core/strava/limiter.py
class StravaRateLimiter:
    """Global gate. All outbound Strava calls pass through this."""
    def acquire(self, cost: int = 1, priority: Priority = Priority.NORMAL) -> None:
        # blocks (with jitter) until budget exists in BOTH windows;
        # reserves headroom below the hard cap for high-priority work
    def observe(self, headers: Mapping[str, str]) -> None:
        # authoritative sync from X-RateLimit-Usage / X-ReadRateLimit-Usage
```

Rules that follow from this:

1. **All Strava calls go through the `strava-sync` queue**, one dedicated worker pool
   with global concurrency capped well under the 15-min limit. No other code path may
   call Strava directly. This is enforceable because the HTTP client lives in one
   module.
2. **Priority lanes.** `INTERACTIVE` (a user clicked "sync now") outranks `WEBHOOK`
   (new activity landed) outranks `BACKFILL` (nightly reconciliation). Reserve ~20%
   of the 15-min budget for the top two lanes so a backfill can never make the
   product feel dead.
3. **429 handling:** honour it as a hard stop for the window, not a retry-in-1s.
   Requeue with `countdown` to the next window boundary + jitter.
4. **Budget accounting per activity:** one webhook activity costs 1 call for the
   summary, +1 if we fetch streams. At 1,000 daily reads that's ~500 new activities
   per day across *all* users — roughly 200–400 active users on the low tier. Track
   `rate_limit_headroom` as a dashboard metric; it is the growth ceiling, and the
   trigger to request a limit increase.

---

## 4. Webhooks — how new activities arrive

Polling every user is the naive approach and it does not fit in the budget: 1,000
users polled hourly is 24,000 calls/day against a 1,000/day limit. Webhooks make
steady-state cost proportional to *activities*, not *users*.

**One subscription per application** (not per athlete), created once:

```
POST https://www.strava.com/api/v3/push_subscriptions
  client_id, client_secret,
  callback_url=https://api.ourdomain.com/webhooks/strava,
  verify_token=<our secret>
```

Strava immediately calls back:

```
GET /webhooks/strava?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…
→ 200 {"hub.challenge": "<echoed>"}      # must match exactly, within seconds
```

Then events arrive as POSTs:

```json
{ "object_type": "activity", "object_id": 1360128428,
  "aspect_type": "create", "owner_id": 134815, "subscription_id": 120475,
  "event_time": 1516126040, "updates": {} }
```

Handler contract — this is strict:

- **Return `200` within 2 seconds.** Strava retries otherwise and will disable a
  subscription that keeps failing. So: validate `subscription_id`, insert into
  `webhook_events`, enqueue, return. Nothing else.
- Handle all three `aspect_type`s: `create` → fetch and insert; `update` → refetch
  (title/type/privacy changed); `delete` → soft-delete our row.
- `object_type: "athlete"` with `updates: {"authorized": "false"}` is a
  **deauthorization** — trigger full data deletion (§6).
- Events can arrive **out of order and more than once.** Processing must be
  idempotent and keyed on `(object_id, aspect_type, event_time)`.
- Webhooks need a **public HTTPS URL**, which is why local dev uses a tunnel
  (`cloudflared` / `ngrok`) and a separate dev Strava app.

### Reconciliation (belt and braces)

Webhooks get missed — deploys, downtime, subscription hiccups. A nightly `beat` job
per user does:

```
GET /athlete/activities?after={sync_state.last_activity_at}&per_page=200
```

`after` is the whole trick: it costs 1 call for a user with no new activities. Only
when the count comes back non-empty do we spend more. Users are spread across the
night by `hash(user_id) % 1440` minutes so the load is flat.

---

## 5. Endpoints we use

| Endpoint | When | Cost |
|---|---|---|
| `GET /athlete` | Login, nightly | 1 |
| `GET /athlete/zones` | Login | 1 — real HR/power zones, better than estimating |
| `GET /athlete/activities?after=&per_page=200` | Reconciliation | 1 per 200 |
| `GET /activities/{id}` | Webhook `create`/`update` | 1 |
| `GET /activities/{id}/streams?keys=…&key_by_type=true` | New activity, if capabilities warrant | 1 |
| `GET /athlete/gear/{id}` | New gear seen | 1 |

Stream keys requested: `time,latlng,distance,altitude,velocity_smooth,heartrate,`
`cadence,watts,temp,moving,grade_smooth`. Strava returns only the ones that exist —
which is itself a capability signal, so we record it.

**We do not fetch segment efforts or leaderboards.** They are expensive in calls,
restricted under the current API terms, and the export already contains the user's
own segment history.

HTTP client: `httpx.AsyncClient` with connection pooling, a 10 s timeout, and
`tenacity` retries on 5xx/timeout only (never on 4xx). Wrapped in
`sp_core/strava/client.py` — the only module allowed to make outbound Strava calls.

---

## 6. <a id="compliance"></a>Compliance and privacy obligations

Not optional, and cheaper to build in than to retrofit:

- **Data is shown only to the athlete who authorized it.** No public sharing, no
  cross-user leaderboards, no exposing one user's activities to another. Our RLS
  model already enforces this.
- **No AI/ML training on Strava data.** The API Agreement prohibits it. We also don't
  send activity data to any third-party LLM without a separate explicit opt-in from
  the user — and if we ever add an "AI coach" feature, it must be opt-in, disclosed,
  and reviewed against the then-current terms.
- **Deauthorization means deletion — of the Strava data, not necessarily the whole
  account.** Since identity is decoupled from Strava ([AUTH.md](AUTH.md)), a user can
  deauthorize Strava while keeping their login and any manually entered data. On the
  athlete-deauthorized webhook (or a user-initiated disconnect): revoke via
  `POST /oauth/deauthorize`, delete the `strava_connections` row, and hard-delete
  every activity/stream/aggregate whose `source` traces back to Strava, plus the
  object-storage objects. Implement as a real cascade with a verification job,
  complete within the window the terms require, and confirm to the user what was and
  wasn't removed — this is distinct from "delete my account," which additionally
  removes the login itself and is described in
  [FRONTEND_DESIGN.md §5](FRONTEND_DESIGN.md#5-settings).
- **Attribution.** "Powered by Strava" and the Strava logo must appear where their
  brand guidelines say. Activities sourced from Strava link back to the Strava
  activity page.
- **Don't mirror the export publicly.** Uploaded archives are private objects,
  presigned-read only, and are lifecycle-deleted from hot storage after 90 days
  (derived data persists).

**The strategic point:** the bulk-export path is not just a backfill mechanism, it's
insurance. If Strava tightens terms or revokes app access, the product still works —
users re-upload their export. Never let a feature become API-only if the export
could support it.

---

## 7. Local development

- Two Strava apps: `strava-premium-dev` (callback → tunnel URL) and
  `strava-premium-prod`.
- `cloudflared tunnel --url http://localhost:8000` for webhook delivery.
- Record real API responses once with `respx`/VCR into
  `tests/fixtures/strava/` and run tests against those. **No test hits the live
  API** — it burns the shared rate limit and makes CI flaky.
- A `make strava:subscribe` / `make strava:unsubscribe` target for managing the dev
  subscription (there can be only one per app, so it needs to be easy to reset).
</content>
</invoke>
