# Local development.
#
# Windows users: run these under Git Bash, or use the raw commands shown in the
# README. Everything here is a thin wrapper — nothing is hidden.

.PHONY: help up down reset migrate api worker web dev test test-int lint fmt check gen-api logs

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:            ## Start postgres, redis and minio
	docker compose up -d

down:          ## Stop the stack (keeps data)
	docker compose down

reset:         ## Stop and DELETE all local data, then start fresh
	docker compose down -v && docker compose up -d

migrate:       ## Apply database migrations
	cd packages/db && ../../.venv/Scripts/python -m alembic upgrade head 2>/dev/null || \
	(cd packages/db && ../../.venv/bin/python -m alembic upgrade head)

api:           ## Run the API on :8000
	./.venv/Scripts/python -m sp_api --host 127.0.0.1 --port 8000 2>/dev/null || \
	./.venv/bin/python -m sp_api --host 127.0.0.1 --port 8000

worker:        ## Run the ingest worker
	./.venv/Scripts/python -m celery -A sp_worker.celery_app worker --loglevel=info --pool=solo -Q ingest 2>/dev/null || \
	./.venv/bin/python -m celery -A sp_worker.celery_app worker --loglevel=info -Q ingest

web:           ## Run the frontend on :5173
	cd apps/web && pnpm dev

test:          ## Unit tests (no services needed)
	./.venv/Scripts/python -m pytest -m "not integration" 2>/dev/null || \
	./.venv/bin/python -m pytest -m "not integration"

test-int:      ## Integration tests (needs `make up`, `make api`, `make worker`)
	./.venv/Scripts/python -m pytest -m integration 2>/dev/null || \
	./.venv/bin/python -m pytest -m integration

lint:          ## Lint and typecheck everything
	./.venv/Scripts/python -m ruff check . && ./.venv/Scripts/python -m ruff format --check .
	cd apps/web && pnpm lint && pnpm typecheck

fmt:           ## Auto-format everything
	./.venv/Scripts/python -m ruff check . --fix && ./.venv/Scripts/python -m ruff format .
	cd apps/web && pnpm format

check: lint test  ## Everything CI runs, minus integration tests

gen-api:       ## Regenerate the frontend's API types (API must be running)
	cd apps/web && pnpm gen:api

logs:          ## Tail the infrastructure logs
	docker compose logs -f
