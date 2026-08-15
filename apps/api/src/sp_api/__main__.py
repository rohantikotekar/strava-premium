"""API entrypoint: ``python -m sp_api``.

Exists because of a Windows-specific incompatibility. Python 3.8+ defaults to
``ProactorEventLoop`` on Windows, and psycopg's async mode cannot run on it. The
event loop is created by uvicorn *before* it imports the application module, so
setting the policy inside ``main.py`` is too late — the loop already exists.

This runner builds a ``SelectorEventLoop`` first and drives uvicorn's server
coroutine on it, which sidesteps uvicorn's own loop setup entirely.

On Linux and macOS this is a plain ``uvicorn.run``.
"""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="sp_api", description="Run the Strava Premium API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    import uvicorn

    config = uvicorn.Config(
        "sp_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )

    if sys.platform == "win32" and not args.reload:
        # Drive the server on a selector loop psycopg can actually use.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(uvicorn.Server(config).serve())
        finally:
            loop.close()
        return

    if sys.platform == "win32":
        # --reload spawns a subprocess, which needs the proactor loop in the
        # supervisor. The child re-enters this module and takes the branch above.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
