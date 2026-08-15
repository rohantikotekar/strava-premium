"""Celery application.

Two queues with separate worker pools, deliberately (ARCHITECTURE.md §4):

* ``ingest``      — CPU-bound decoding. Scale by adding replicas.
* ``strava_sync`` — I/O-bound and globally rate-limited. Must not share a pool with
  ingest, or a big import starves the sync that keeps accounts live.
"""

from __future__ import annotations

from celery import Celery
from sp_core.config import get_settings

settings = get_settings()

celery_app = Celery("sp_worker", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Tasks must be safe to retry — Celery *will* retry them (CLAUDE.md §4.2).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # CPU-bound work: a big prefetch just makes one worker hoard the queue while
    # its siblings idle.
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    result_expires=3600,
    task_default_queue="ingest",
    task_routes={
        "sp_worker.ingest.*": {"queue": "ingest"},
        "sp_worker.metrics.*": {"queue": "ingest"},
        "sp_worker.strava.*": {"queue": "strava_sync"},
    },
)

# Import for side effects: task registration.
celery_app.autodiscover_tasks(["sp_worker.tasks"], force=True)

from sp_worker.tasks import ingest, metrics  # noqa: E402,F401
