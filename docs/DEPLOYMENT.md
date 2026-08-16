# Deployment

## The pick

| Layer | Service | Why |
|---|---|---|
| Frontend | **Cloudflare Workers Builds** (formerly Pages) | Free, global CDN, git-push deploys, same account as R2 (no cross-service egress) |
| API + worker | **Railway** | Deploys this repo's containers as-is; managed Postgres + Redis; one dashboard; public HTTPS out of the box (required for Strava webhooks later) |
| Object storage | **Cloudflare R2** | Already the architecture's pick — zero egress fees, S3-compatible, so `S3_ENDPOINT` is the only thing that changes from local MinIO |
| DNS | **Cloudflare** | Same account as R2; free SSL |

This is the fast lane from [ARCHITECTURE.md §6](ARCHITECTURE.md#6-scaling-path) ("Prod v1: Fly.io or Railway... without a Kubernetes tax"). Nothing here is a new architectural decision — it's picking one of the two options already named and making it concrete.

---

## AWS + Cloudflare alternative

For a solo project or early testing, before paying for Railway:

| Layer | Service | Cost | Catch |
|---|---|---|---|
| Frontend | **Cloudflare Workers Builds** (formerly Pages) | Free, unlimited | None |
| Postgres + Redis + API + worker | **One AWS EC2 instance**, running this repo's `docker compose --profile full` as-is | Free-tier eligible for 12 months (legacy accounts) or via signup credits (accounts created July 2025+); **~$12–15/month after** — EC2 has no permanent free tier, unlike Oracle's Always Free shapes | Not free forever — budget for it once the trial window/credits run out |
| Public HTTPS for that VM | **Cloudflare Tunnel** (free) | Unlimited | Replaces opening security-group ports / managing TLS certs yourself — the tunnel handles HTTPS |
| Object storage | **Cloudflare R2 free tier** | 10 GB storage, 1M reads + 1M writes/month, egress always free | Kept regardless of which cloud runs compute — R2 was picked for zero egress fees ([ARCHITECTURE.md:159](ARCHITECTURE.md#L159)), switching to S3 here would reintroduce exactly the egress cost that decision avoided |
| Domain | Skip it — Workers gives a free `*.workers.dev` URL | $0 | A real domain is ~$10/yr whenever you want one |

**Total: $0/month during the free-tier/credit window, ~$12–15/month after** — this is the honest number; see [the AWS section below](#the-honest-tradeoff) for why EC2 can't match Oracle's free-forever deal.

**Known gap, not yet mitigated:** Postgres runs self-hosted in a container on the EC2 instance's disk — no managed backups, no multi-AZ. CLAUDE.md §4.3's "raw data is rebuildable from object storage" safety net covers activity data (it's re-derivable from the R2 Parquet files) but **not** user accounts, password hashes, or connected Strava tokens, which exist only in that one Postgres container. Losing the EC2 volume loses every account. Worth adding a scheduled `pg_dump` → R2 backup before real users sign up; out of scope for this doc as written.

`infra/api.Dockerfile` and `infra/worker.Dockerfile` (the images the `full` profile builds) were test-built and run end to end against this stack — signup, upload, ingest, charts — before writing the steps below, so this is a verified path, not a guess.

### The fast path: one script

Steps 2 ("AWS EC2 — the VM") and 3–4 ("Deploy the app" / "Cloudflare Tunnel")
below are all scriptable except one unavoidable OAuth click, and
[`infra/bootstrap-aws-vm.sh`](../infra/bootstrap-aws-vm.sh) does them for
you: installs Docker, clones the repo, writes `.env` from prompts (generating
`SESSION_SECRET`/`TOKEN_ENCRYPTION_KEY`/the Postgres password itself), brings
the stack up, runs the migration, and installs the Cloudflare Tunnel as a
systemd service. It's idempotent — re-running it after filling in a domain
you left blank the first time picks up where it left off.

You still do steps 1 (R2 bucket) and the "launch the instance" half of step 2 by
hand — those are dashboard clicks, not shell commands, so there's nothing to
script. Once you've SSHed into the fresh instance:

```bash
curl -fsSL https://raw.githubusercontent.com/rohantikotekar/strava-premium/main/infra/bootstrap-aws-vm.sh -o bootstrap.sh
chmod +x bootstrap.sh
./bootstrap.sh
```

It'll ask for the R2 credentials from step 1 and (optionally) your domain.
Then do step 5 (Cloudflare Workers Builds) — a dashboard click-through, not
scriptable either — and you're done.

The manual walkthrough below is what the script is automating — read it if
you want to understand what's happening, or if something in the script fails
and you need to finish a step by hand.

### Step-by-step (manual — see the fast path above)

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

#### 2. AWS EC2 — the instance (~10 minutes)

1. Sign up / log into the [AWS Console](https://console.aws.amazon.com). Free-tier terms depend on account age — see the table above.
2. **EC2 → Launch instance**:
   - **Name**: `strava-premium`
   - **AMI**: Ubuntu Server 22.04 LTS — arm64 if using a Graviton (`t4g.*`) type, x86 for `t2.*`/`t3.*`.
   - **Instance type**: two workable choices —
     - `t4g.small` (2 vCPU, 2GB RAM) — comfortable headroom, no tuning needed. Free-tier eligible only for accounts created July 2025+.
     - `t2.micro`/`t3.micro` (1 vCPU, 1GB RAM) — the classic 12-month-free legacy shape, fine for a handful of users. [`infra/bootstrap-aws-vm.sh`](../infra/bootstrap-aws-vm.sh) auto-detects instances under 1.5GB RAM and adds a 2GB swap file plus caps Celery to `--concurrency=1`, so a large bulk-export import degrades to slower instead of getting OOM-killed. No manual tuning needed either way — the script handles both sizes.
   - **Key pair**: create a new one and download the `.pem` — this is what you SSH in with.
   - **Network settings**: leave the default security group (inbound SSH/port 22 only). No need to open 80/443 — Cloudflare Tunnel (step 4 below) means you never expose a port publicly.
   - **Storage**: bump to 20GB gp3 (default 8GB is thin for Docker images + Postgres data).
   - **Launch instance**. Note the **public IPv4 address**.
3. SSH in: `ssh -i your-key.pem ubuntu@<public-ip>`
4. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   newgrp docker
   ```

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
3. Bring the stack up — name the four services explicitly so Compose skips `minio`/`minio-init` (those are local-dev-only stand-ins for R2 and would just burn RAM for nothing here):
   ```bash
   docker compose --profile full up -d --build postgres redis api worker
   ```
   This builds `infra/api.Dockerfile` and `infra/worker.Dockerfile` and starts Postgres, Redis, the API, and the worker — four containers, one VM. If you're on a 1GB instance (`t2.micro`/`t3.micro`), set `WORKER_CONCURRENCY=1` in `.env` first and add a 2GB swap file — see the script's automatic handling of this if you'd rather not do it by hand.
4. Run the migration, from inside the running API container (so it uses the internal Docker network — Postgres is never exposed to the internet). `alembic.ini`'s `script_location = migrations` resolves relative to the working directory Alembic runs from, not the `-c` path, so this has to run from `packages/db` (matching how the [Makefile](../Makefile) does it locally) rather than the container's default `/app`:
   ```bash
   docker compose exec -w /app/packages/db api python -m alembic upgrade head
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

#### 5. Cloudflare Workers Builds — the frontend

Cloudflare has folded Pages into unified **Workers Builds** — same free static hosting, different setup screen. `apps/web/wrangler.jsonc` (already in the repo) tells it what to serve.

1. Dashboard → **Compute (Workers & Pages)** → **Create application** → **Import a repository** → this repo.
2. Settings:
   - Root directory: `apps/web`
   - Build command: `pnpm install && pnpm build`
   - Deploy command: leave the default `npx wrangler deploy`
   - Non-production branches: leave the default `npx wrangler versions upload` — this gives every non-main push its own preview URL, same role classic Pages' branch previews played
   - API token: click **Create new token**, accept the auto-generated one — Workers deploys need a token to authenticate, Cloudflare scopes it automatically
3. Add a build-time environment variable: `VITE_API_BASE=https://api.yourdomain.com`. In dev, [api.ts](../apps/web/src/lib/api.ts) talks to `/api` through Vite's proxy (so the session cookie stays first-party); in prod there's no such proxy, so it reads this var instead and calls the tunnel URL directly.
4. Deploy. You get a `*.workers.dev` URL immediately; add `app.yourdomain.com` under the Worker's **Settings → Domains & Routes** once ready.

#### 6. Verify

Open `https://app.yourdomain.com`, sign up, and run through Import with a real (or the synthetic) export — same flow as local dev, just on the real internet now.

### The honest tradeoff

One instance means **no redundancy** — if it reboots or AWS reclaims/stops it, the whole backend is down until you notice and restart the containers (Docker restarts containers automatically on an instance reboot; an actual instance termination needs you to redo step 2/3). Fine for personal use or a beta; move to the Railway plan above the moment real users depend on uptime. Nothing about the migration is a rewrite — same Docker images, same env vars, just swap where they run.

Unlike Oracle's Always Free tier, **this isn't free indefinitely** — EC2's free allowance is a 12-month window (legacy accounts) or a prepaid credit balance (accounts created July 2025+). Budget ~$12–15/month once that runs out. If $0-forever is the hard requirement rather than "cheap and easy," Oracle Cloud's Always Free `A1.Flex` shape is still the only genuinely permanent free option among mainstream providers — this AWS path trades that guarantee for AWS's more familiar console and IAM/tooling ecosystem.

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
6. Run the migration once, from your machine, pointed at the Railway Postgres URL. Same `script_location`-is-relative-to-cwd gotcha as the AWS/Oracle path — run it from inside `packages/db`, not the repo root:
   ```bash
   cd packages/db && DATABASE_URL=<railway postgres url> uv run alembic upgrade head
   ```
   Same non-superuser-role requirement applies as local dev — run [infra/postgres-init.sql](../infra/postgres-init.sql)'s `sp_app` role creation against the Railway database first (Railway's default user is enough of a superuser to bypass RLS otherwise — CLAUDE.md §4.5).

### 3. Cloudflare Workers Builds (frontend)

Same steps as the free-tier walkthrough's [step 5](#5-cloudflare-workers-builds--the-frontend) — Cloudflare folded Pages into unified Workers Builds, `apps/web/wrangler.jsonc` already configures it:

1. **Compute (Workers & Pages)** → Create application → Import a repository → this repo.
2. Root directory `apps/web`, build command `pnpm install && pnpm build`, deploy command left at its default `npx wrangler deploy`.
3. Environment variable: point the Vite dev-proxy equivalent at production — actually simpler, since in prod the frontend calls `API_BASE_URL` directly rather than through Vite's proxy. Add a build-time env var (`VITE_API_BASE`) or a small runtime config if you want to avoid a rebuild per API URL change (out of scope for v1 — hardcoding `https://api.yourdomain.com` at build time is fine to start).
4. Attach `app.yourdomain.com` under the Worker's **Settings → Domains & Routes**.

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
