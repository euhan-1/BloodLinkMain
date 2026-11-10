"""A Server-Timing response header, so the browser's DevTools can separate time spent on the server from time spent on the network.

    Server-Timing: app;dur=412.3, db;dur=268.9

Durations in milliseconds and nothing else: no query text, no counts, no table names, no paths.

  app  the whole time this process spent on the request, from receiving it to starting the response (routing, auth, the handler,
       its queries, building the JSON).
  db   the part of that spent waiting on database statements, on BOTH engines (the normal one and the autocommit read engine). It adds
       up the statement round trips only: opening a connection from the pool (the pre-ping), BEGIN, COMMIT and ROLLBACK are separate
       round trips that are NOT counted here, so `app - db` includes them as well as the Python work. A read-only endpoint has none of
       those extras, so its `db` is nearly all of its time.

On by default; SERVER_TIMING=false (or 0, no, off) turns it off. Importing this module has no side effects: main.py calls
install_db_timing() and adds the middleware only when enabled().
"""
import os
import time
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import event
from starlette.datastructures import MutableHeaders

# One list per request, set by the middleware. A sync endpoint runs in a worker thread, which gets a copy of the request's context
# with the same list object in it, so the statement hooks below can add to it from there.
_db_seconds: ContextVar[Optional[list]] = ContextVar("server_timing_db_seconds", default=None)


def enabled() -> bool:
    return os.environ.get("SERVER_TIMING", "true").strip().lower() not in ("false", "0", "no", "off")


def install_db_timing(engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _statement_started(conn, cursor, statement, parameters, context, executemany):
        context._server_timing_t0 = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _statement_finished(conn, cursor, statement, parameters, context, executemany):
        acc = _db_seconds.get()
        started = getattr(context, "_server_timing_t0", None)
        if acc is not None and started is not None:
            acc[0] += time.perf_counter() - started


class ServerTimingMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so it adds no per-request task of its own."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        db = [0.0]
        token = _db_seconds.set(db)

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                total_ms = (time.perf_counter() - started) * 1000
                MutableHeaders(scope=message).append("Server-Timing", f"app;dur={total_ms:.1f}, db;dur={db[0] * 1000:.1f}")
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            _db_seconds.reset(token)
