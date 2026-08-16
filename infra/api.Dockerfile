# Production image for the API service.
#
# Build from the REPO ROOT, not this directory — a uv workspace needs every
# member's pyproject.toml present to resolve the local path dependencies
# (sp_core, sp_db), so the whole repo is the build context:
#
#   docker build -f infra/api.Dockerfile -t sp-api .

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Manifests first: dependency install becomes a cached layer that only
# invalidates when a pyproject.toml or the lockfile actually changes, not on
# every source edit.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/db/pyproject.toml packages/db/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --frozen --no-install-workspace --package sp-api

COPY packages/core packages/core
COPY packages/db packages/db
COPY apps/api apps/api
RUN uv sync --frozen --package sp-api

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["python", "-m", "sp_api", "--host", "0.0.0.0", "--port", "8000"]
