"""The Server-Timing header: durations only, on by default, switchable off, and the database share is counted from the worker
thread a sync endpoint runs in. Pure ASGI and an in-memory SQLite engine, so no database server is needed."""
import asyncio
import re
import unittest
from unittest import mock

from sqlalchemy import create_engine, text

import server_timing

SHAPE = re.compile(r"^app;dur=\d+\.\d, db;dur=\d+\.\d$")


async def _call(app):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, send)
    return sent


def _run(app):
    sent = asyncio.run(_call(app))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode(): v.decode() for k, v in start["headers"]}


def _ok_app(work=None):
    async def app(scope, receive, send):
        if work:
            await asyncio.to_thread(work)  # like a sync endpoint: a worker thread with a copy of the request's context
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


class ServerTimingTests(unittest.TestCase):
    def test_header_is_durations_only(self):
        headers = _run(server_timing.ServerTimingMiddleware(_ok_app()))
        self.assertRegex(headers["server-timing"], SHAPE)
        self.assertEqual(headers["content-type"], "text/plain")  # the response itself is untouched

    def test_database_time_is_counted_from_a_worker_thread(self):
        engine = create_engine("sqlite://")
        server_timing.install_db_timing(engine)

        def work():
            with engine.connect() as c:
                for i in range(200):  # enough that the total survives rounding to 0.1 ms
                    c.execute(text("SELECT :i"), {"i": i})

        headers = _run(server_timing.ServerTimingMiddleware(_ok_app(work)))
        app_ms, db_ms = (float(x) for x in re.findall(r"dur=(\d+\.\d)", headers["server-timing"]))
        self.assertGreater(db_ms, 0.0)
        self.assertLessEqual(db_ms, app_ms)

    def test_a_statement_outside_any_request_is_ignored(self):
        engine = create_engine("sqlite://")
        server_timing.install_db_timing(engine)
        with engine.connect() as c:  # no request context: must not raise
            c.execute(text("SELECT 1"))

    def test_requests_do_not_share_a_total(self):
        engine = create_engine("sqlite://")
        server_timing.install_db_timing(engine)

        def work():
            with engine.connect() as c:
                for i in range(200):
                    c.execute(text("SELECT :i"), {"i": i})

        busy = _run(server_timing.ServerTimingMiddleware(_ok_app(work)))
        idle = _run(server_timing.ServerTimingMiddleware(_ok_app()))
        self.assertGreater(float(re.search(r"db;dur=(\d+\.\d)", busy["server-timing"]).group(1)), 0.0)
        self.assertEqual(re.search(r"db;dur=(\d+\.\d)", idle["server-timing"]).group(1), "0.0")

    def test_only_http_is_wrapped(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        asyncio.run(server_timing.ServerTimingMiddleware(app)({"type": "lifespan"}, None, None))
        self.assertEqual(seen, ["lifespan"])

    def test_switch(self):
        for value, expected in (("", True), ("true", True), ("TRUE", True), ("false", False), ("0", False), ("off", False)):
            with mock.patch.dict("os.environ", {"SERVER_TIMING": value}):
                self.assertEqual(server_timing.enabled(), expected, value)
        with mock.patch.dict("os.environ", clear=True):
            self.assertTrue(server_timing.enabled(), "unset means on")
