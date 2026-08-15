# Authentication

## The core decision

**Identity is decoupled from Strava.** Account creation/login use email+password or
Google OAuth. Connecting Strava (to pull activity data) is a separate step, done
*after* an account exists, via the OAuth flow in
[STRAVA_API.md §2](STRAVA_API.md#2-oauth-20).

Why this changed from "Strava OAuth is our only identity provider": Strava caps new
apps at a very small number of connected athletes until you request an increase (see
[STRAVA_API.md §1](STRAVA_API.md#1-app-registration)), and that review can be slow.
If Strava connection *is* login, nobody can even create an account past that cap.
Decoupling means signup is never blocked by Strava's approval process, and a user
whose Strava token gets revoked doesn't lose access to their account or their
already-imported history.

A user can hold **any combination** of: a password, a linked Google identity, a
linked Strava connection. All three map to one `users` row.

---

## 1. Identity model

```sql
CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               CITEXT UNIQUE NOT NULL,
    email_verified_at   TIMESTAMPTZ,
    password_hash       TEXT,                -- NULL if the user only ever used Google
    first_name          TEXT,
    last_name            TEXT,
    profile_photo_url   TEXT,
    -- fitness profile (unchanged)
    weight_kg           NUMERIC(5,2),
    ftp_w               INTEGER,
    max_hr_bpm          INTEGER,
    resting_hr_bpm      INTEGER,
    measurement_pref    TEXT DEFAULT 'metric',
    timezone            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ,
    deleted_at          TIMESTAMPTZ
);

-- One row per external identity a user has linked. A user may have 0 or 1 of each
-- provider today (Google), but this shape also covers Apple/GitHub later without
-- a migration.
CREATE TABLE auth_identities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,           -- 'google'
    provider_sub    TEXT NOT NULL,           -- Google's stable `sub` claim — NEVER the email
    email_at_link   CITEXT NOT NULL,         -- snapshot, for audit only
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_sub)
);

-- Strava is a DATA CONNECTION, not an identity. Same shape as before, just no
-- longer the thing users log in with. strava_athlete_id lives here, not on users.
CREATE TABLE strava_connections (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    strava_athlete_id   BIGINT UNIQUE NOT NULL,
    access_token_enc    BYTEA NOT NULL,
    refresh_token_enc   BYTEA NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    scopes              TEXT[] NOT NULL,
    connected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- the cookie value
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    ip_created_from INET,
    user_agent      TEXT,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX ON sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE email_verification_tokens (
    token_hash   TEXT PRIMARY KEY,           -- sha256 of the token; raw token only ever in the email link
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at   TIMESTAMPTZ NOT NULL,        -- 24h
    used_at      TIMESTAMPTZ
);

CREATE TABLE password_reset_tokens (
    token_hash   TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at   TIMESTAMPTZ NOT NULL,        -- 1h
    used_at      TIMESTAMPTZ,
    requested_ip INET
);

-- Every login/signup/reset attempt, success or failure. Drives lockout + alerts.
CREATE TABLE auth_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    email_tried CITEXT,                      -- kept even on failure/unknown-email
    event       TEXT NOT NULL,               -- signup|login_password|login_google|
                                              -- login_failed|logout|password_reset|
                                              -- lockout
    ip          INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON auth_events (email_tried, created_at DESC);
CREATE INDEX ON auth_events (ip, created_at DESC);
```

`strava_connections` no longer being tied 1:1 with row creation means a user can
sign up, use the app with an API-only recent-activity view, and connect/import
Strava whenever they're ready — matching the onboarding flow in
[FRONTEND_DESIGN.md](FRONTEND_DESIGN.md#1-first-run--the-onboarding-that-decides-everything).

---

## 2. Sign up / log in — manual (email + password)

**Signup**

```
POST /auth/signup   { email, password }
```

1. Normalize + validate email format. **Do not reveal whether the email is already
   registered** in the response — respond identically either way and let the
   *email itself* disclose it ("you already have an account — log in instead" is
   sent to the inbox, not the API caller). This is the standard mitigation for
   account-enumeration.
2. Validate password against policy (below).
3. Hash with **Argon2id** (`argon2-cffi`), OWASP-2025 baseline parameters:
   `time_cost=2, memory_cost=19*1024 (19 MiB), parallelism=1`. Re-tune to the
   deploy target's hardware; the point is a memory-hard hash, not a specific number.
4. Insert `users` row (`email_verified_at = NULL`), fire a verification email.
5. Create a session immediately — **don't block product access on verification**,
   but gate anything sensitive (connecting Strava, exporting data, changing email)
   behind `email_verified_at IS NOT NULL`.

**Password policy** — NIST 800-63B, not legacy complexity rules:

- Minimum **12 characters**. No forced uppercase/digit/symbol composition rules —
  they push users toward predictable patterns and don't measurably help.
- Maximum length generous (≥256 chars) so passphrases aren't penalized.
- Checked against the **Have I Been Pwned** range API
  (`k-anonymity`: send only the first 5 hex chars of the SHA-1 hash, never the
  password or full hash) — reject if it appears in a known breach corpus.
- No forced periodic rotation (also NIST guidance — rotation policies produce
  weaker passwords, not stronger ones).

**Login**

```
POST /auth/login   { email, password }
```

- Look up by email; if absent, run the Argon2 verify against a **fixed dummy hash**
  anyway before returning the generic "invalid email or password" — this keeps
  response timing (and therefore email enumeration via timing) roughly constant.
- On success: new `sessions` row, httpOnly/Secure/SameSite=Lax cookie, log
  `auth_events`.
- On failure: log it, and apply **progressive rate limiting** — see §5.

**Password reset**

```
POST /auth/password-reset/request   { email }     → always 200, generic message
POST /auth/password-reset/confirm   { token, new_password }
```

Reset token: 32 random bytes, only the **sha256 of it** stored (so a DB read alone
can't be replayed as a valid token), 1-hour expiry, single-use, and successful reset
**revokes every existing session** for that user (a reset is very often recovery
from a compromised account — don't leave the attacker's session alive).

---

## 3. Sign up / log in — Google OAuth (OIDC)

```
GET /auth/google/start
    → redirects to Google's OAuth consent screen
    scope=openid email profile
    state=<csrf token, stored server-side against the pending session>

GET /auth/google/callback?code=…&state=…
```

1. Verify `state` matches what we issued (CSRF protection on the OAuth dance itself).
2. Exchange `code` for tokens; **verify the returned `id_token`'s signature** against
   Google's published JWKS (library: `google-auth`), checking `aud`, `iss`, `exp`.
3. Read `sub` (Google's stable, non-reusable user id), `email`, `email_verified`.
4. Resolution:

   | Condition | Action |
   |---|---|
   | `auth_identities` row exists for `(google, sub)` | Log in as that user |
   | No identity row, but a `users` row with this email already exists (password or another provider) | **Do not silently auto-link.** Show: *"An account already exists for this email. Log in, then link Google from Settings."* Auto-linking on email match is a known account-takeover vector — an attacker who controls a Google account with a victim's (unverified) email could hijack the victim's password account otherwise. |
   | No identity row, no `users` row | Create `users` (email pre-verified — Google's `email_verified` claim satisfies this, no verification email needed) + `auth_identities` row. This is "sign up with Google." |

5. New session, same cookie mechanics as manual login.

Linking Google to an already-logged-in account (`POST /auth/google/link`) reuses the
same callback but requires an active session and checks the email match explicitly
before inserting the `auth_identities` row.

---

## 4. Sessions

- **Opaque server-side sessions**, not JWTs-in-cookie. A JWT can't be revoked before
  expiry without a blocklist, which is just a worse version of a sessions table we
  need anyway (for "log out everywhere", reset-revokes-sessions, and account
  deletion). The cost — a DB/Redis lookup per request — is cached in Redis with a
  short TTL, keyed on session id.
- Cookie: `httpOnly`, `Secure`, `SameSite=Lax`, scoped to the API domain. The Strava
  access/refresh tokens **never** reach the browser — same rule as before, now
  living in `strava_connections` instead of `strava_tokens`.
- Sliding expiry: `expires_at` extended on activity, hard cap 30 days; absolute
  re-auth required after that regardless of activity.
- `GET /me/sessions` + `DELETE /me/sessions/{id}` — users can see and revoke active
  sessions (device/IP/last-seen), and "log out of all other devices" is one call.
- State-changing endpoints also check `Origin`/`Referer` against an allowlist as a
  second layer beyond `SameSite=Lax` (belt-and-braces against older browsers and
  misconfigured proxies).

---

## 5. Abuse controls

| Control | Detail |
|---|---|
| **Progressive login throttling** | Per `(email)` and per `(ip)`, tracked in Redis. 5 failures → 30s delay, doubling up to a 15 min cap. Never a permanent lockout — that's a denial-of-service vector against a known email. |
| **Signup throttling** | Per IP, capped (e.g. 10/hour) to blunt mass account creation. |
| **CAPTCHA** | Triggered (not default-on) after repeated failures on signup or login from one IP — Cloudflare Turnstile, privacy-respecting, no Google reCAPTCHA data-sharing concerns. |
| **Enumeration resistance** | Signup, login, and password-reset responses never distinguish "no such account" from "wrong credentials" in the API response. |
| **`auth_events` monitoring** | Feeds an alert on anomalous patterns (spray attacks: many emails, one IP, one password-shaped timing). |

---

## 6. What's explicitly deferred

| Deferred | To when |
|---|---|
| Multi-factor auth (TOTP) | v2 — add `mfa_totp_secret_enc` + a recovery-codes table; do **not** build SMS MFA (SIM-swap risk, and it's what NIST 800-63B specifically deprecates) |
| Magic-link (passwordless) login | v2, if password-reset-as-login patterns show demand |
| Apple / GitHub sign-in | v2+, the `auth_identities` shape already supports it with zero schema change |
| Passkeys / WebAuthn | v3 — genuinely the best long-term answer, deferred only because it's a bigger UI lift than the timeline allows for v1 |

---

## 7. What changes elsewhere

- [DATA_MODEL.md](DATA_MODEL.md) — `users`/`strava_tokens` section replaced by the
  schema in §1 above.
- [STRAVA_API.md §2](STRAVA_API.md#2-oauth-20) — the OAuth flow described there is
  now **"connect Strava"**, performed by an already-authenticated user, not login.
- [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md#1-first-run--the-onboarding-that-decides-everything) —
  onboarding gains a signup/login screen before the Strava-connect step.
- [CLAUDE.md §8](../CLAUDE.md#8-security--privacy) — updated to reflect password
  storage and session handling.
</content>
</invoke>
