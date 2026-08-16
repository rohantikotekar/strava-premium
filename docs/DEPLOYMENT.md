# Deployment

## The pick

| Layer | Service | Why |
|---|---|---|
| Frontend | **Cloudflare Pages** | Free, global CDN, git-push deploys, same account as R2 (no cross-service egress) |
| API + worker | **Railway** | Deploys this repo's containers as-is; managed Postgres + Redis; one dashboard; public HTTPS out of the box (required for Strava webhooks later) |
| Object storage | **Cloudflare R2** | Already the architecture's pick — zero egress fees, S3-compatible, so `S3_ENDPOINT` is the only thing that changes from local MinIO |
| DNS | **Cloudflare** | Same account as R2; free SSL |

This is the fast lane from [ARCHITECTURE.md §6](ARCHITECTURE.md#6-scaling-path) ("Prod v1: Fly.io or Railway... without a Kubernetes tax"). Nothing here is a new architectural decision — it's picking one of the two options already named and making it concrete.

---

## Free-tier alternative ($0/month)

For a solo project or early testing, before paying anything:

| Layer | Service | Free limit | Catch |
|---|---|---|---|
| Frontend | **Cloudflare Pages** | Unlimited, always free | None |
| Postgres + Redis + API + worker | **One Oracle Cloud "Always Free" VM**, running this repo's `docker compose --profile full` as-is | 4 ARM cores / 24 GB RAM, forever — not a trial | Needs a credit card at signup for identity verification (never charged on the free shapes). Free ARM capacity is sometimes hard to get in a region on first signup — retry if it says "out of capacity." |
| Public HTTPS for that VM | **Cloudflare Tunnel** (free) | Unlimited | Replaces opening firewall ports / managing TLS certs yourself — the tunnel handles HTTPS |
| Object storage | **Cloudflare R2 free tier** | 10 GB storage, 1M reads + 1M writes/month, egress always free (even past free tier) | Fine for a solo user; only relevant limit is storage past ~10 GB of everyone's raw exports |
| Domain | Skip it — Pages gives a free `*.pages.dev` URL | $0 | A real domain is ~$10/yr whenever you want one |

**Total: $0/month, indefinitely** — this isn't a trial that expires.

`infra/api.Dockerfile` and `infra/worker.Dockerfile` (the images the `full` profile builds) were test-built and run end to end against this stack — signup, upload, ingest, charts — before writing the steps below, so this is a verified path, not a guess.

### Step-by-step

#### 1. Cloudflare — R2 bucket (10 minutes)

1. Sign up at [cloudflare.com](https://cloudflare.com) (free).
2. Dashboard → **R2** → **Create bucket** → name it `strava-premium-prod`.
3. R2 → **Manage API tokens** → **Create API token** → permissions: **Object Read & Write**, scoped to that bucket. Copy the **Access Key ID** and **Secret Access Key** — R2 shows the secret once.
4. Note your **Account ID** (R2 dashboard, right sidebar) — your endpoint is `https://<account-id>.r2.cloudflarestorage.com`.
5. Bucket → **Settings** → **CORS Policy** → add:
   ```json
   [
     {
       "AllowedOrigins": ["https://app.yourdomain.com", "https://*.pages.dev"],
       "AllowedMethods": ["GET", "PUT"],
       "AllowedHeaders": ["*"]
     }
   ]
   ```
   (Browsers upload/download stream files directly to R2 via presigned URLs — this is what allows that cross-origin request. See [CLAUDE.md §8](../CLAUDE.md#8-security--privacy).)

#### 2. Oracle Cloud — the VM (15–20 minutes, plus possible retries for capacity)

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) for an **Always Free** account. Requires a card for identity verification; the free-tier shapes are never billed.
2. **Compute → Instances → Create Instance**.
   - Image: **Ubuntu 22.04** (or latest LTS).
   - Shape: **Ampere (ARM) A1.Flex** — set 4 OCPUs / 24 GB (the full free allowance). If it says out of capacity, retry in a different Availability Domain or later — this is the single most common snag with Oracle's free tier.
   - Add your SSH public key (generate one with `ssh-keygen` if you don't have one).
   - Create. Note the **public IP**.
3. SSH in: `ssh ubuntu@<public-ip>`
4. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   newgrp docker
   ```
5. Oracle's default network security list blocks inbound traffic by default — but with Cloudflare Tunnel (step 4 below) you never need to open a port publicly, so leave the firewall closed. Only allow SSH (already open by default on the Oracle-created security list).

#### 3. Deploy the app onto the VM

1. Clone and configure:
   ```bash
   git clone https://github.com/rohantikotekar/strava-premium.git
   cd strava-premium
   cp .env.example .env
   nano .env
   ```
2. Fill in `.env` for real (not the local-dev defaults):
   ```
   DATABASE_URL=postgresql+psycopg://sp_app:<pick-a-real-password>@postgres:5432/strava_premium
   REDIS_URL=redis://redis:6379/0
   S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
   S3_PUBLIC_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
   S3_BUCKET=strava-premium-prod
   S3_ACCESS_KEY=<from R2 step 3>
   S3_SECRET_KEY=<from R2 step 3>
   S3_REGION=auto
   SESSION_SECRET=<openssl rand -base64 48>
   TOKEN_ENCRYPTION_KEY=<python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
   ENVIRONMENT=production
   COOKIE_SECURE=true
   API_BASE_URL=https://api.yourdomain.com
   WEB_BASE_URL=https://app.yourdomain.com
   CORS_ORIGINS=https://app.yourdomain.com
   ```
   Also update the matching password in `infra/postgres-init.sql` (`CREATE ROLE sp_app WITH LOGIN PASSWORD '...'`) to the same real password before first boot — that file only runs once, on the Postgres volume's first init.
3. Bring the whole stack up:
   ```bash
   docker compose --profile full up -d --build
   ```
   This builds `infra/api.Dockerfile` and `infra/worker.Dockerfile` and starts Postgres, Redis, the API, and the worker — five containers, one VM.
4. Run the migration, from inside the running API container (so it uses the internal Docker network — Postgres is never exposed to the internet):
   ```bash
   docker compose exec api python -m alembic -c /app/packages/db/alembic.ini upgrade head
   ```
5. Sanity check: `curl http://localhost:8000/health` should return `{"status":"ok"}`.

#### 4. Cloudflare Tunnel — public HTTPS without opening a port

1. On the VM: `curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o cloudflared && chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/`
2. `cloudflared tunnel login` — opens a URL, authorize it against your Cloudflare account in a browser.
3. `cloudflared tunnel create strava-premium-api`
4. Route it: `cloudflared tunnel route dns strava-premium-api api.yourdomain.com`
5. Point it at the API container: create `~/.cloudflared/config.yml`
   ```yaml
   tunnel: strava-premium-api
   credentials-file: /home/ubuntu/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: api.yourdomain.com
       service: http://localhost:8000
     - service: http_status:404
   ```
6. Run it as a service so it survives reboots: `sudo cloudflared service install && sudo systemctl start cloudflared`
7. `curl https://api.yourdomain.com/health` from your own machine should now return `{"status":"ok"}` — this confirms the tunnel end to end.

#### 5. Cloudflare Pages — the frontend

1. Dashboard → **Workers & Pages** → **Create** → **Pages** → **Connect to Git** → this repo.
2. Build settings:
   - Root directory: `apps/web`
   - Build command: `pnpm install && pnpm build`
   - Output directory: `dist`
3. Add a Pages build-time environment variable: `VITE_API_BASE=https://api.yourdomain.com`. In dev, [api.ts](../apps/web/src/lib/api.ts) talks to `/api` through Vite's proxy (so the session cookie stays first-party); in prod there's no such proxy, so it reads this var instead and calls the tunnel URL directly.
4. Deploy. Pages gives you `<project>.pages.dev` immediately; add `app.yourdomain.com` under **Custom domains** once ready.

#### 6. Verify

Open `https://app.yourdomain.com`, sign up, and run through Import with a real (or the synthetic) export — same flow as local dev, just on the real internet now.

### The honest tradeoff

One VM means **no redundancy** — if it reboots or Oracle reclaims it (rare on Always Free, but it's not an SLA), the whole backend is down until you notice and restart the containers. Fine for personal use or a beta; move to the Railway plan above the moment real users depend on uptime. Nothing about the migration is a rewrite — same Docker images, same env vars, just swap where they run.

---

## Launch checklist

### 1. Cloudflare account (R2 + DNS + Pages — one signup covers all three)

1. Create an R2 bucket: `strava-premium-prod`.
2. R2 → Manage API tokens → create one scoped to that bucket (read+write). Save the access key + secret.
3. R2 bucket → Settings → CORS: allow `PUT`, `GET` from your frontend's domain (needed for direct browser upload/download).
4. Add your domain to Cloudflare (or buy one through them — ~$10/yr for a `.com`).

### 2. Railway (API + worker + Postgres + Redis)

1. New Railway project → **Deploy from GitHub repo** → point at this repo.
2. Add a **Postgres** plugin and a **Redis** plugin (one click each). Railway wires the connection strings in automatically as env vars.
3. Add two services from the same repo, different start commands:
   - `api`: `python -m sp_api --host 0.0.0.0 --port $PORT`
   - `worker`: `celery -A sp_worker.celery_app worker --loglevel=info -Q ingest`

   (Linux target, so no `--pool=solo` — that flag is Windows-only, from the local-dev workaround in [README.md](../README.md#notes-for-windows).)
4. Set env vars on **both** services (Railway lets you share a variable group):

   ```
   DATABASE_URL          <- from the Postgres plugin, but swap driver to +psycopg
   REDIS_URL             <- from the Redis plugin
   S3_ENDPOINT           https://<account-id>.r2.cloudflarestorage.com
   S3_PUBLIC_ENDPOINT     same, or a custom R2 domain if you set one up
   S3_BUCKET             strava-premium-prod
   S3_ACCESS_KEY / S3_SECRET_KEY   <- from the R2 API token
   S3_REGION             auto
   SESSION_SECRET         openssl rand -base64 48
   TOKEN_ENCRYPTION_KEY   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ENVIRONMENT            production
   COOKIE_SECURE          true
   API_BASE_URL           https://api.yourdomain.com
   WEB_BASE_URL           https://app.yourdomain.com
   CORS_ORIGINS           https://app.yourdomain.com
   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET     (once you have them)
   STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET     (once you have them)
   ```

5. Railway generates a `*.up.railway.app` URL immediately — attach `api.yourdomain.com` as a custom domain once ready (Railway → Settings → Domains, then a CNAME in Cloudflare).
6. Run the migration once, from your machine, pointed at the Railway Postgres URL:
   ```bash
   DATABASE_URL=<railway postgres url> uv run alembic -c packages/db/alembic.ini upgrade head
   ```
   Same non-superuser-role requirement applies as local dev — run [infra/postgres-init.sql](../infra/postgres-init.sql)'s `sp_app` role creation against the Railway database first (Railway's default user is enough of a superuser to bypass RLS otherwise — CLAUDE.md §4.5).

### 3. Cloudflare Pages (frontend)

1. Pages → Create project → connect the same GitHub repo.
2. Build settings:
   - Root directory: `apps/web`
   - Build command: `pnpm install && pnpm build`
   - Output directory: `dist`
3. Environment variable: point the Vite dev-proxy equivalent at production — actually simpler, since in prod the frontend calls `API_BASE_URL` directly rather than through Vite's proxy. Add a build-time env var or a small runtime config if you want to avoid a rebuild per API URL change (out of scope for v1 — hardcoding `https://api.yourdomain.com` in an env file read at build time is fine to start).
4. Attach `app.yourdomain.com` as a custom domain (Pages → Custom domains).

### 4. Point Strava at production (once OAuth/webhooks land — currently schema-only, see [STRAVA_API.md](STRAVA_API.md))

- Authorization Callback Domain: `api.yourdomain.com`
- Webhook subscription callback: `https://api.yourdomain.com/webhooks/strava`

---

## Cost at v1 scale (roughly, a few hundred users)

| Item | Cost |
|---|---|
| Railway (API + worker + Postgres + Redis) | ~$10–20/mo on the Hobby/Pro plan |
| Cloudflare R2 | ~$0.015/GB/mo storage, **$0 egress** — a few dollars/mo even with real usage |
| Cloudflare Pages | Free |
| Domain | ~$10–15/yr |

Call it **$15–30/month** to run this for real, not a toy.

---

## Scaling path

This mirrors [ARCHITECTURE.md §6](ARCHITECTURE.md#6-scaling-path):

| Signal | Move |
|---|---|
| Worker queue backing up under import load | Add worker replicas in Railway (one slider, no code change) |
| Need finer autoscaling, multi-region, or outgrow Railway's limits | Move `api`/`worker` to **Fly.io** — same Dockerfiles, same env var shape, a config-file change not a rewrite |
| Postgres CPU-bound on dashboard queries | Add a Railway/managed read replica; route chart reads to it |
| R2 bill grows | It won't meaningfully — egress is the cost driver for this app's access pattern (browser reading stream Parquet directly) and R2 doesn't charge for it |

The one thing to get right *before* launch, because it's not a later config change: confirm the Strava app's athlete-limit increase request (see [STRAVA_API.md §1](STRAVA_API.md#1-app-registration)) is in before you need more than a handful of connected accounts — that approval has a human review turnaround.

---

## What this doc deliberately skips

- **CI/CD beyond what exists.** [.github/workflows/ci.yml](../.github/workflows/ci.yml) already lints/tests/builds on every push; wiring Railway/Pages auto-deploy on top of that (both support "deploy on push to main" natively) is a dashboard checkbox, not something to script.
- **Custom Kubernetes/Terraform.** Explicitly out of scope until the Fly.io stage stops being enough — see ARCHITECTURE.md's stance on this.
</content>
</invoke>
