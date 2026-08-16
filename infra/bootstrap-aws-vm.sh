#!/usr/bin/env bash
# One-shot setup for the AWS + Cloudflare deploy (docs/DEPLOYMENT.md, "AWS EC2
# + Cloudflare" section). Run this ONCE, right after SSHing into a fresh
# EC2 Ubuntu 22.04 instance. It replaces the manual steps in DEPLOYMENT.md
# §3 "Deploy the app onto the VM" and §4 "Cloudflare Tunnel" with one script:
# Docker install, clone, .env, docker compose up, migration, and the Tunnel
# (everything in the Tunnel step that CAN be scripted — the
# `cloudflared tunnel login` line still opens a browser for you to click
# "authorize", because that's an OAuth step Cloudflare requires a human for).
#
# You still need to do FIRST, before running this:
#   1. Launch the EC2 instance (docs/DEPLOYMENT.md §2) and SSH in.
#      t4g.small (2GB RAM) is the comfortable size. If you're staying on a
#      1GB free-tier instance (t2.micro/t3.micro) instead, this script adds
#      a 2GB swap file and caps worker concurrency at 1 automatically —
#      that's the mitigation for the memory spike a large bulk-export
#      import causes, not something you need to configure by hand.
#   2. Create the R2 bucket + API token (docs/DEPLOYMENT.md §1) — have the
#      account ID, access key, and secret key ready. Object storage stays on
#      Cloudflare R2 even in the AWS plan — it's what ARCHITECTURE.md picked
#      for zero egress fees, and that has nothing to do with which cloud
#      runs the compute.
#   3. Own a domain added to Cloudflare (only needed if you want the Tunnel
#      step now; you can skip it and come back to it later).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<you>/strava-premium/main/infra/bootstrap-aws-vm.sh -o bootstrap.sh
#   chmod +x bootstrap.sh
#   ./bootstrap.sh
#
# Safe to re-run: each step checks whether it already happened and skips.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/rohantikotekar/strava-premium.git}"
REPO_DIR="${REPO_DIR:-$HOME/strava-premium}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ask() { # ask "prompt" "default"
  local prompt="$1" default="${2:-}" reply
  if [ -n "$default" ]; then
    read -rp "$prompt [$default]: " reply
    echo "${reply:-$default}"
  else
    read -rp "$prompt: " reply
    echo "$reply"
  fi
}
ask_secret() {
  local prompt="$1" reply
  read -rsp "$prompt: " reply
  echo >&2
  echo "$reply"
}

bold "== 0/6: Swap =="
# Cheap insurance for small instances (t2.micro/t3.micro's 1GB): a memory
# spike during a large bulk-export import gets slowed down by swapping
# instead of triggering the OOM killer. No-op on instances with enough RAM
# that they'd never touch it.
if swapon --show | grep -q .; then
  echo "Swap already active, skipping."
elif [ -f /swapfile ]; then
  sudo swapon /swapfile
  echo "Existing /swapfile re-activated."
else
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
  echo "2GB swap file created and enabled (persists across reboots via /etc/fstab)."
fi

bold "== 1/6: Docker =="
if command -v docker >/dev/null 2>&1; then
  echo "Docker already installed, skipping."
else
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. If later commands in this script fail with a"
  echo "permission error, log out, log back in, and re-run this script —"
  echo "the group membership above needs a fresh shell to take effect."
fi
# Use sudo for docker in THIS run so we don't require a re-login mid-script.
DC="sudo docker compose"

bold "== 2/6: Clone the repo =="
if [ -d "$REPO_DIR/.git" ]; then
  echo "$REPO_DIR already exists, skipping clone."
else
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

bold "== 3/6: Collect config =="
if [ -f .env ]; then
  echo ".env already exists — leaving it as-is. Delete it first if you want"
  echo "to redo this step."
else
  echo "Enter your R2 details (from docs/DEPLOYMENT.md §1)."
  echo "Tip: if pasting into the masked secret-key prompt is giving you"
  echo "trouble over SSH, Ctrl+C out and instead run:"
  echo "  export R2_SECRET_KEY='paste-here'"
  echo "then re-run this script — it'll skip the prompt and use that instead."
  R2_ACCOUNT_ID=$(ask "R2 Account ID")
  R2_BUCKET=$(ask "R2 bucket name" "strava-premium-prod")
  R2_ACCESS_KEY=$(ask "R2 Access Key ID")
  R2_SECRET_KEY="${R2_SECRET_KEY:-$(ask_secret "R2 Secret Access Key")}"
  echo
  echo "Domains (leave blank to fill in later, e.g. before the Tunnel step):"
  API_DOMAIN=$(ask "API domain, e.g. api.yourdomain.com" "")
  WEB_DOMAIN=$(ask "Web domain, e.g. app.yourdomain.com" "")
  API_BASE_URL="${API_DOMAIN:+https://$API_DOMAIN}"
  WEB_BASE_URL="${WEB_DOMAIN:+https://$WEB_DOMAIN}"

  # Auto-detect low-RAM instances (t2.micro/t3.micro's 1GB) and cap Celery
  # concurrency at 1 so a bulk-export import never holds two parser
  # processes' buffers in memory at once — see the swap-file step above.
  TOTAL_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [ "$TOTAL_MB" -lt 1536 ]; then
    WORKER_CONCURRENCY=1
    echo "Detected ${TOTAL_MB}MB RAM — capping WORKER_CONCURRENCY=1."
  else
    WORKER_CONCURRENCY=2
  fi

  PG_PASSWORD=$(openssl rand -hex 24)
  SESSION_SECRET=$(openssl rand -base64 48)
  # A Fernet key IS urlsafe_b64encode(32 random bytes) — that's the whole
  # implementation, so this matches Fernet.generate_key() without needing
  # the cryptography package installed on the host.
  TOKEN_KEY=$(openssl rand 32 | base64 | tr '+/' '-_')

  cat > .env <<EOF
DATABASE_URL=postgresql+psycopg://sp_app:${PG_PASSWORD}@postgres:5432/strava_premium
REDIS_URL=redis://redis:6379/0
S3_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
S3_PUBLIC_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
S3_BUCKET=${R2_BUCKET}
S3_ACCESS_KEY=${R2_ACCESS_KEY}
S3_SECRET_KEY=${R2_SECRET_KEY}
S3_REGION=auto
SESSION_SECRET=${SESSION_SECRET}
TOKEN_ENCRYPTION_KEY=${TOKEN_KEY}
ENVIRONMENT=production
COOKIE_SECURE=true
API_BASE_URL=${API_BASE_URL}
WEB_BASE_URL=${WEB_BASE_URL}
CORS_ORIGINS=${WEB_BASE_URL}
WORKER_CONCURRENCY=${WORKER_CONCURRENCY}
EOF
  chmod 600 .env
  echo ".env written."

  # infra/postgres-init.sql only runs on the Postgres volume's first boot —
  # its password must match .env's DATABASE_URL before that first boot.
  sed -i "s/sp_dev_password/${PG_PASSWORD}/" infra/postgres-init.sql
  echo "infra/postgres-init.sql password synced to .env."
fi

bold "== 4/6: Bring the stack up =="
# Only postgres, redis, api, worker — minio/minio-init are local-dev-only
# (object storage is R2 here) and would just burn RAM for nothing on a
# small instance. Compose starts their dependencies automatically, so
# naming these four is enough.
$DC --profile full up -d --build postgres redis api worker

bold "Waiting for the API to become healthy..."
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "API is up."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "API did not become healthy after 5 minutes. Check logs:"
    echo "  $DC logs api --tail 100"
    exit 1
  fi
  sleep 10
done

bold "== 5/6: Run the migration =="
$DC exec -T api python -m alembic -c /app/packages/db/alembic.ini upgrade head

bold "== 6/6: Cloudflare Tunnel (public HTTPS, no open security-group ports) =="
if [ -z "${API_DOMAIN:-}" ]; then
  echo "No API domain was set in step 3, so skipping the Tunnel setup."
  echo "Once you have a domain on Cloudflare, re-run this script (it will"
  echo "reuse the existing .env) or follow docs/DEPLOYMENT.md §4 by hand."
else
  if command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared already installed, skipping install."
  else
    ARCH=$(uname -m)
    case "$ARCH" in
      aarch64|arm64) CF_ARCH=arm64 ;;
      x86_64) CF_ARCH=amd64 ;;
      *) echo "Unrecognized arch $ARCH — install cloudflared manually." && exit 1 ;;
    esac
    curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" -o cloudflared
    chmod +x cloudflared
    sudo mv cloudflared /usr/local/bin/
  fi

  if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
    bold "One manual step: a login URL is about to print. Open it in any"
    bold "browser and click Authorize — this is Cloudflare's own OAuth,"
    bold "there's no way to script around it."
    cloudflared tunnel login
  fi

  TUNNEL_NAME="strava-premium-api"
  if ! cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    cloudflared tunnel create "$TUNNEL_NAME"
  fi
  cloudflared tunnel route dns "$TUNNEL_NAME" "$API_DOMAIN" || true

  TUNNEL_ID=$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n{print $1}')
  mkdir -p "$HOME/.cloudflared"
  cat > "$HOME/.cloudflared/config.yml" <<EOF
tunnel: ${TUNNEL_NAME}
credentials-file: ${HOME}/.cloudflared/${TUNNEL_ID}.json
ingress:
  - hostname: ${API_DOMAIN}
    service: http://localhost:8000
  - service: http_status:404
EOF

  sudo cloudflared service install
  sudo systemctl restart cloudflared
  echo "Tunnel installed as a systemd service — survives reboots."
fi

bold "Done."
echo "Backend: curl https://${API_DOMAIN:-<no domain set>}/health should return {\"status\":\"ok\"}"
echo "Next: deploy the frontend on Cloudflare Workers Builds (docs/DEPLOYMENT.md §5) —"
echo "that part is a dashboard click-through, nothing to script."
