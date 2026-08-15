"""FastAPI application.

The HTTP layer does no heavy work — handlers validate, enqueue, and return
(CLAUDE.md §4.1).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sp_core.config import get_settings

from sp_api.routers import activities, auth, charts, me, uploads

# Windows defaults to ProactorEventLoop, which psycopg's async mode cannot use.
# This must run before uvicorn creates the loop, hence module import time.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

#: Never let these reach a log sink. This app holds a decade of precise location
#: history (CLAUDE.md §8).
_REDACTED_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "lat",
        "lng",
        "latitude",
        "longitude",
        "email",
    }
)


def _redact(_logger: object, _name: str, event: dict[str, object]) -> dict[str, object]:
    for key in list(event):
        if key.lower() in _REDACTED_KEYS:
            event[key] = "[redacted]"
    return event


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.dev.ConsoleRenderer()
            if get_settings().environment == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

    # Make the bucket exist so a fresh clone works without manual setup.
    try:
        from sp_core.storage.objects import ensure_bucket

        ensure_bucket()
    except Exception as exc:
        log.warning("startup.bucket_unavailable", error=str(exc))

    log.info(
        "startup",
        environment=settings.environment,
        google_enabled=settings.google_enabled,
        strava_enabled=settings.strava_enabled,
    )
    yield
    log.info("shutdown")


app = FastAPI(
    title="Strava Premium API",
    version="0.1.0",
    description="Self-hosted analytics over your own Strava data.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,  # required: the session is a cookie
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """Second layer of CSRF defence beyond SameSite=Lax (AUTH.md §4).

    Checks Origin on state-changing requests. SameSite covers modern browsers;
    this covers the rest, and misconfigured proxies.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        allowed = set(get_settings().cors_origin_list)
        if origin and origin not in allowed:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Cross-origin request rejected."},
            )
    return await call_next(request)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(me.router)
app.include_router(uploads.router)
app.include_router(activities.router)
app.include_router(charts.router)
