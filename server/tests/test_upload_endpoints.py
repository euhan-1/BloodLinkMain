"""The inventory and historical uploads are plain functions (they run in FastAPI's threadpool, not on the event loop), and a
historical upload starts order selection in the background only when AUTO_ORDER_SELECTION_AFTER_UPLOAD is true.

The donor upload is deliberately NOT changed by this batch (it stays `async def`); a test pins that so it is not converted by accident.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import contextlib
import inspect
import io
import json
import time
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from fastapi import BackgroundTasks
from sqlalchemy import text
from starlette.datastructures import UploadFile

import auth
import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair

HEADER = "din,blood_type,component,location,volume_ml,collected_date,expires_date\n"
SKIP_LINE = "[order-selection] skipped after upload"


def route_endpoint(path):
    return next(r.endpoint for r in app.app.routes if getattr(r, "path", None) == path)


class HandlerKindTests(unittest.TestCase):
    def test_the_inventory_and_historical_uploads_are_plain_functions(self):
        for path in ("/inventory/upload", "/forecast/historical-upload"):
            self.assertFalse(inspect.iscoroutinefunction(route_endpoint(path)), path)

    def test_the_donor_upload_is_left_exactly_as_it_was(self):
        self.assertTrue(inspect.iscoroutinefunction(route_endpoint("/donors/upload")))


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)

    def historical(self, csv=None, bg=None):
        csv = csv if csv is not None else f"snapshot_date,blood_type,units\n{TODAY - timedelta(days=1)},O+,10\n"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            body = app.upload_historical_inventory_snapshots(
                file=UploadFile(file=io.BytesIO(csv.encode()), filename="h.csv"),
                facility_id=self.bank, uploaded_by=None, background_tasks=bg)
        return body, out.getvalue()

    def test_the_variable_defaults_to_off(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertFalse(app._auto_order_selection_enabled())

    def test_only_an_explicit_true_turns_it_on(self):
        for value, expected in (("true", True), ("TRUE", True), (" true ", True), ("1", True), ("yes", True), ("on", True),
                                ("false", False), ("0", False), ("", False), ("no", False), ("maybe", False)):
            with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": value}):
                self.assertEqual(app._auto_order_selection_enabled(), expected, repr(value))

    def test_off_an_upload_schedules_nothing_and_logs_one_line(self):
        bg = BackgroundTasks()
        with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": "false"}), \
                mock.patch.object(app, "_run_order_selection_for_facility") as run:
            body, log = self.historical(bg=bg)
        self.assertEqual(len(bg.tasks), 0)
        run.assert_not_called()
        lines = [l for l in log.splitlines() if l.startswith(SKIP_LINE)]
        self.assertEqual(len(lines), 1, log)
        self.assertIn(f"facility {self.bank}", lines[0])
        self.assertIn("AUTO_ORDER_SELECTION_AFTER_UPLOAD", lines[0])

    def test_on_an_upload_schedules_selection_and_logs_no_skip(self):
        bg = BackgroundTasks()
        with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": "true"}):
            _, log = self.historical(bg=bg)
        self.assertEqual([t.args for t in bg.tasks], [(self.bank, app.MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN)])
        self.assertNotIn(SKIP_LINE, log)

    def test_an_upload_that_stored_nothing_logs_nothing(self):
        bg = BackgroundTasks()
        with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": "false"}):
            body, log = self.historical(csv="snapshot_date,blood_type,units\n", bg=bg)
        self.assertEqual(body["rows_processed"], 0)
        self.assertNotIn(SKIP_LINE, log)

    def test_the_response_is_the_same_whether_it_is_on_or_off(self):
        results = []
        for value in ("false", "true"):
            with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": value}):
                body, _ = self.historical(bg=BackgroundTasks())
            results.append(body)
        for body in results:
            self.assertEqual(set(body), {"rows_processed", "errors", "days_of_history", "min_days_required"})
        self.assertEqual(results[0], results[1])

    def test_a_missing_background_tasks_object_is_still_fine(self):
        with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": "false"}):
            body, log = self.historical(bg=None)  # how the other tests (and direct callers) call it
        self.assertEqual(body["rows_processed"], 1)
        self.assertNotIn(SKIP_LINE, log)


def multipart(filename, content):
    boundary = "xBOUNDARYx"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: text/csv\r\n\r\n{content}\r\n--{boundary}--\r\n").encode()
    return body, f"multipart/form-data; boundary={boundary}"


async def asgi(method, path, headers=(), body=b""):
    """One request through the real ASGI app (routing, auth dependency, UploadFile and BackgroundTasks injection) -> (status, body)."""
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(60)  # a real server would wait here; the response finishes first

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http", "server": ("test", 80),
             "client": ("test", 1), "headers": [(b"host", b"test"), *[(k.encode(), v.encode()) for k, v in headers]]}
    await app.app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


class ThroughTheRealAppTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)
        with engine.begin() as c:  # upload_history.uploaded_by is a foreign key to a real user (cleaned up with the facility)
            user_id = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                {"e": f"upl-{self.tag}@example.test", "f": self.bank}).scalar()
        self.addCleanup(self.clear_uploads)  # runs before make_pair's own cleanup, which deletes the user these rows point at
        token = auth.create_access_token(user_id=user_id, email=f"upl-{self.tag}@example.test", facility_id=self.bank, role="staff")
        self.auth = [("authorization", f"Bearer {token}")]

    def clear_uploads(self):
        with engine.begin() as c:
            for t in ("blood_units", "inventory_snapshot_write_log", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.bank})

    def csv(self):
        col = TODAY - timedelta(days=2)
        return HEADER + "\n".join(f"{self.tag}-{n},O+,Packed RBC,Fridge A,450,{col},{TODAY + timedelta(days=20)}" for n in range(3))

    def post_upload(self, path, content):
        body, ctype = multipart("u.csv", content)
        return asgi("POST", path, [*self.auth, ("content-type", ctype)], body)

    def test_an_inventory_upload_works_end_to_end_with_the_same_response(self):
        status, body = asyncio.run(self.post_upload("/inventory/upload", self.csv()))
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result["rows_processed"], 3)
        self.assertEqual(result["errors"], [])
        self.assertEqual(set(result), {"rows_processed", "errors"})  # what it always returned

    def test_a_non_csv_file_is_still_refused(self):
        body, ctype = multipart("u.txt", "x")
        status, _ = asyncio.run(asgi("POST", "/inventory/upload", [*self.auth, ("content-type", ctype)], body))
        self.assertEqual(status, 400)

    def test_a_historical_upload_works_end_to_end_and_skips_selection_by_default(self):
        rows = f"snapshot_date,blood_type,units\n{TODAY - timedelta(days=1)},O+,10\n"
        with mock.patch.dict("os.environ", {"AUTO_ORDER_SELECTION_AFTER_UPLOAD": "false"}), \
                mock.patch.object(app, "_run_order_selection_for_facility") as run:
            status, body = asyncio.run(self.post_upload("/forecast/historical-upload", rows))
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["rows_processed"], 1)
            run.assert_not_called()

    def test_a_slow_upload_does_not_hold_up_other_requests(self):
        """The reason for the conversion: while an upload parses (blocking), the event loop must still answer other requests."""
        asyncio.run(asgi("GET", "/health"))  # warms the connection pool, so the timing below is only the request

        real = app._parse_inventory_csv

        def slow_parse(text_):
            time.sleep(0.8)  # the blocking CSV/database work of a big file
            return real(text_)

        async def scenario():
            with mock.patch.object(app, "_parse_inventory_csv", slow_parse):
                t0 = time.perf_counter()
                upload = asyncio.ensure_future(self.post_upload("/inventory/upload", self.csv()))
                await asyncio.sleep(0.2)  # the upload is now inside slow_parse (if the loop is free to tell)
                other_status, _ = await asgi("GET", "/health")
                other_done = time.perf_counter() - t0  # measured from the start: a blocked loop cannot even start the second request
                upload_status, _ = await upload
                return other_status, other_done, upload_status, time.perf_counter() - t0

        other_status, other_done, upload_status, total = asyncio.run(scenario())
        self.assertEqual((other_status, upload_status), (200, 200))
        self.assertGreater(total, 0.8)       # the upload really was slow
        self.assertLess(other_done, 0.5)     # and the other request was answered while it ran, not after
