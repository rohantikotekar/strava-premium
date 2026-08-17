/**
 * Typed API client.
 *
 * Bootstrap note: these types are hand-written so the app can be built before the
 * server exists. Once the API is running, `pnpm gen:api` regenerates
 * `src/lib/api/schema.d.ts` from the OpenAPI document and these should be replaced
 * by the generated ones (CLAUDE.md §3). If the UI needs a field, add it to the
 * Pydantic response model first.
 */

// "/api" in dev — Vite proxies it to the local API (vite.config.ts) so the
// session cookie stays first-party. In production there is no such proxy, so
// a real deployment sets VITE_API_BASE (e.g. https://api.yourdomain.com) as a
// build-time env var — see DEPLOYMENT.md § Cloudflare Pages.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/**
 * Bearer-token fallback for cross-site deployments.
 *
 * The session is normally an httpOnly cookie that JS never touches. But when the
 * API lives on an unrelated registrable domain (a *.workers.dev frontend calling
 * a *.trycloudflare.com API), browsers drop that cookie as third-party no matter
 * what SameSite says, so the server can be told to also return the token in the
 * signup/login body and accept it as `Authorization: Bearer`.
 *
 * Storing it here means XSS can read it — see the server's `auth_bearer_tokens`
 * setting. When the server leaves that off, nothing below ever fires: no token
 * comes back, none is stored, and the cookie keeps doing the work.
 */
const TOKEN_KEY = "sp_session_token";

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage disabled (private mode, blocked cookies)
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Non-fatal: without storage the cookie path is the only one, which is fine.
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 401 drives a redirect to /login rather than an error toast. */
  get isUnauthorized() {
    return this.status === 401;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // The session is an httpOnly cookie; without this it is never sent.
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // Non-JSON error body: keep the status text.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;

  const data: unknown = await response.json();
  // Signup and login are the only endpoints that ever carry a token, and only
  // when the server has the bearer path enabled. Capturing it centrally keeps
  // every other call site unaware that any of this exists.
  if (data !== null && typeof data === "object" && "session_token" in data) {
    const token = (data as { session_token?: unknown }).session_token;
    if (typeof token === "string" && token) setSessionToken(token);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---- types -----------------------------------------------------------------

export interface User {
  id: string;
  email: string;
  email_verified: boolean;
  first_name: string | null;
  last_name: string | null;
  profile_photo_url: string | null;
  measurement_pref: "metric" | "imperial";
  weight_kg: number | null;
  ftp_w: number | null;
  max_hr_bpm: number | null;
  resting_hr_bpm: number | null;
  sex: string | null;
  has_password: boolean;
  has_google: boolean;
  strava_connected: boolean;
  created_at: string;
}

export interface AuthProviders {
  google: boolean;
  strava: boolean;
}

export interface Capability {
  capability: string;
  activity_count: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface CapabilitiesResponse {
  capabilities: Capability[];
  total_activities: number;
  first_activity: string | null;
  last_activity: string | null;
  sports: string[];
}

export interface ChartMeta {
  chart_id: string;
  title: string;
  question: string;
  unit: string | null;
  is_estimate: boolean;
  estimate_reason: string | null;
  coverage_note: string | null;
  activities_used: number;
  activities_total: number;
}

export interface ChartSeries {
  key: string;
  label: string;
  points: Record<string, number | string | null>[];
}

export interface ChartResponse {
  meta: ChartMeta;
  series: ChartSeries[];
}

export interface StatTile {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  delta_pct: number | null;
  sparkline: number[];
}

export interface DashboardSummary {
  period_label: string;
  hero: StatTile;
  tiles: StatTile[];
  streak_days: number;
  longest_streak_days: number;
  active_days: number;
}

export interface ActivityListItem {
  id: string;
  strava_activity_id: number | null;
  name: string | null;
  sport_type: string;
  sport_group: string;
  start_time_local: string;
  elapsed_time_s: number;
  moving_time_s: number | null;
  distance_m: number | null;
  elevation_gain_m: number | null;
  avg_hr_bpm: number | null;
  avg_power_w: number | null;
  avg_speed_mps: number | null;
  training_load: number | null;
  load_source: string | null;
  has_streams: boolean;
  is_indoor: boolean;
}

export interface ActivityDetail extends ActivityListItem {
  description: string | null;
  max_hr_bpm: number | null;
  max_speed_mps: number | null;
  max_power_w: number | null;
  avg_cadence_rpm: number | null;
  normalized_power_w: number | null;
  intensity_factor: number | null;
  efficiency_factor: number | null;
  decoupling_pct: number | null;
  tss: number | null;
  trimp: number | null;
  calories: number | null;
  elevation_loss_m: number | null;
  polyline: string | null;
  start_lat: number | null;
  start_lng: number | null;
  available_channels: string[];
  is_commute: boolean;
  zone_time: Record<string, Record<string, number>>;
  best_efforts: Record<string, Record<string, number>>;
  distance_prs: Record<string, number>;
}

export interface ActivityPage {
  items: ActivityListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ImportStatus {
  id: string;
  status: string;
  filename: string | null;
  items_total: number;
  items_done: number;
  items_failed: number;
  activities_found: number;
  error: string | null;
  created_at: string;
  fast_path_done_at: string | null;
  completed_at: string | null;
}

export interface UploadCreated {
  upload_id: string;
  upload_url: string;
  method: string;
  object_key: string;
}

export interface StreamsResponse {
  activity_id: string;
  n_samples: number;
  channels: Record<string, (number | null)[]>;
}
