# Production image for the Celery worker.
#
# Build from the REPO ROOT: docker build -f infra/worker.Dockerfile -t sp-worker .

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/db/pyproject.toml packages/db/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --frozen --no-install-workspace --package sp-worker

COPY packages/core packages/core
COPY packages/db packages/db
COPY apps/worker apps/worker
RUN uv sync --frozen --package sp-worker

ENV PATH="/app/.venv/bin:$PATH"

# Real prefork pool here, unlike the Windows-only --pool=solo used in local
# dev (README § Notes for Windows) — this is what lets multiple activity
# files parse in parallel (ARCHITECTURE.md §4, ingest workers scale by
# replicas/concurrency).
CMD ["celery", "-A", "sp_worker.celery_app", "worker", "--loglevel=info", "-Q", "ingest", "--concurrency=2"]
