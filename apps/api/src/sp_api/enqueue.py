"""Task enqueueing.

The API must never import from the worker (CLAUDE.md §2) — that one-way dependency
is what lets workers move to their own machines without a refactor. So we send by
**task name** through a bare Celery client that knows nothing about the task code.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from celery import Celery
from sp_core.config import get_settings

# Queue names must match the worker's routing (sp_worker.celery_app).
QUEUE_INGEST = "ingest"
QUEUE_SYNC = "strava_sync"


@lru_cache
def _client() -> Celery:
    settings = get_settings()
    return Celery(broker=settings.redis_url, backend=settings.redis_url)


def enqueue(task_name: str, *args: Any, queue: str = QUEUE_INGEST, **kwargs: Any) -> str:
    """Send a task by name. Returns the task id."""
    result = _client().send_task(task_name, args=args, kwargs=kwargs, queue=queue)
    return str(result.id)


def enqueue_import(upload_id: str, user_id: str) -> str:
    return enqueue("sp_worker.ingest.inspect_upload", upload_id, user_id, queue=QUEUE_INGEST)


def enqueue_recompute(user_id: str) -> str:
    return enqueue("sp_worker.metrics.recompute_user", user_id, queue=QUEUE_INGEST)
