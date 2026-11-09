"""The upload size cap (MAX_UPLOAD_BYTES, default 5 MB) and the file-type check, for the inventory and historical uploads. The donor upload
is deliberately outside the cap and is asserted to be left alone. Needs the scratch database (conftest.py)."""
import asyncio
import io
import json
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import text
from starlette.datastructures import UploadFile

import auth
import main as app
import upload_limits as ul
from database import engine
from tests.test_expired_transfer import TODAY, make_pair
from tests.test_upload_endpoints import asgi, multipart

CSV_HEADER = "din,blood_type,component,location,volume_ml,collected_date,expires_date\n"


def upload_file(data: bytes, name="u.csv"):
    return UploadFile(file=io.BytesIO(data), filename=name)


class CountingFile(io.BytesIO):
    """Records how many bytes were actually read from it."""

    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, n=-1):
        out = super().read(n)
        self.bytes_read += len(out)
        return out


class LimitSettingTests(unittest.TestCase):
    def test_default_is_five_megabytes_and_it_is_configurable(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertEqual(ul.max_upload_bytes(), 5 * 1024 * 1024)
        for value, expected in (("1000", 1000), (" 2048 ", 2048), ("", 5 * 1024 * 1024), ("abc", 5 * 1024 * 1024),
                                ("0", 5 * 1024 * 1024), ("-5", 5 * 1024 * 1024)):
            with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": value}):
                self.assertEqual(ul.max_upload_bytes(), expected, repr(value))

    def test_the_message_is_plain_language_with_the_limit_in_it(self):
        self.assertEqual(ul.too_large_message(5 * 1024 * 1024),
                         "That file is too large. Files can be up to 5 MB; split it into smaller files and upload them one at a time.")
        self.assertIn("1.5 MB", ul.too_large_message(int(1.5 * 1024 * 1024)))
        self.assertIn("2 KB", ul.too_large_message(2048))


class ReadCsvUploadTests(unittest.TestCase):
    def test_a_normal_file_is_read_and_a_bom_is_tolerated(self):
        self.assertEqual(ul.read_csv_upload(upload_file("a,b\n1,2\n".encode())), "a,b\n1,2\n")
        self.assertEqual(ul.read_csv_upload(upload_file(b"\xef\xbb\xbfa,b\n")), "a,b\n")

    def test_a_file_exactly_at_the_limit_is_accepted_and_one_byte_over_is_refused(self):
        self.assertEqual(len(ul.read_csv_upload(upload_file(b"x" * 1000), limit=1000)), 1000)
        with self.assertRaises(HTTPException) as ctx:
            ul.read_csv_upload(upload_file(b"x" * 1001), limit=1000)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("too large", ctx.exception.detail)

    def test_an_oversize_file_is_never_read_past_one_byte_over_the_limit(self):
        big = CountingFile(b"x" * (10 * 1024 * 1024))
        with self.assertRaises(HTTPException) as ctx:
            ul.read_csv_upload(UploadFile(file=big, filename="big.csv"), limit=1024 * 1024)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(big.bytes_read, 1024 * 1024 + 1)  # it stopped; it did not load the 10 MB

    def test_the_extension_is_checked(self):
        for name in ("u.txt", "u.xlsx", "csv", ""):
            with self.assertRaises(HTTPException) as ctx:
                ul.read_csv_upload(upload_file(b"a,b\n", name))
            self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (400, "only .csv files are accepted"), name)

    def test_the_bytes_are_checked_not_only_the_name(self):
        for label, data in (("xlsx (a zip)", b"PK\x03\x04" + b"\x00" * 50), ("pdf", b"%PDF-1.7 ..."), ("old xls", b"\xd0\xcf\x11\xe0" + b"1" * 20),
                            ("exe", b"MZ\x90\x00"), ("png", b"\x89PNG\r\n\x1a\n"), ("gzip", b"\x1f\x8b\x08"), ("NUL bytes", b"a,b\n1,\x002\n")):
            with self.assertRaises(HTTPException) as ctx:
                ul.read_csv_upload(upload_file(data))
            self.assertEqual(ctx.exception.status_code, 400, label)
            self.assertIn("not a plain-text CSV", ctx.exception.detail, label)

    def test_non_utf8_text_is_still_refused_with_the_old_message(self):
        with self.assertRaises(HTTPException) as ctx:
            ul.read_csv_upload(upload_file("caf\xe9,1\n".encode("latin-1")))
        self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (400, "file must be UTF-8 encoded"))


async def run_through(middleware_app, headers, chunks):
    """Drives the middleware with a stand-in app. Returns (status, body, how many body chunks the inner app was handed)."""
    sent, delivered = [], {"n": 0}
    queue = list(chunks)

    async def receive():
        if queue:
            delivered["n"] += 1
            return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/inventory/upload", "headers": headers}
    await middleware_app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"), delivered["n"]


def reading_app(on_error="propagate"):
    """An inner app that reads the whole body, like the form parser, and answers 200. With on_error="400" it turns a failed read into a
    400, which is what FastAPI does with any exception raised while parsing a body."""
    calls = {"n": 0}

    async def inner(scope, receive, send):
        calls["n"] += 1
        try:
            while True:
                message = await receive()
                if message["type"] != "http.request" or not message.get("more_body"):
                    break
        except Exception:
            if on_error == "400":
                await send({"type": "http.response.start", "status": 400, "headers": []})
                await send({"type": "http.response.body", "body": b"There was an error parsing the body"})
                return
            raise
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    inner.calls = calls
    return inner


class MiddlewareTests(unittest.TestCase):
    PATHS = ("/inventory/upload", "/forecast/historical-upload")

    def build(self, inner):
        return ul.UploadSizeLimitMiddleware(inner, paths=self.PATHS)

    def test_a_declared_length_over_the_limit_is_refused_before_any_body_is_read(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):
            inner = reading_app()
            status, body, delivered = asyncio.run(run_through(self.build(inner), [(b"content-length", str(50 * 1024 * 1024).encode())], [b"x"]))
        self.assertEqual(status, 413)
        self.assertIn("too large", json.loads(body)["detail"])
        self.assertEqual((inner.calls["n"], delivered), (0, 0))   # the app was never started and not one body chunk was read

    def test_with_no_declared_length_it_stops_reading_when_the_running_total_passes_the_limit(self):
        chunk = b"x" * 40_000
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):   # limit = 1000 + 64 KB of multipart allowance
            for on_error in ("propagate", "400"):
                inner = reading_app(on_error)
                status, body, delivered = asyncio.run(run_through(self.build(inner), [], [chunk] * 10))
                self.assertEqual(status, 413, on_error)               # even when the app itself turns the cut-off into a 400
                self.assertIn("too large", json.loads(body)["detail"])
                self.assertLessEqual(delivered, 2, on_error)          # it did not keep reading the other eight

    def test_a_body_under_the_limit_passes_through_untouched(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):
            inner = reading_app()
            status, body, _ = asyncio.run(run_through(self.build(inner), [(b"content-length", b"500")], [b"x" * 500]))
        self.assertEqual((status, body, inner.calls["n"]), (200, b"ok", 1))

    def test_other_paths_and_methods_and_the_donor_upload_are_not_touched(self):
        huge = [(b"content-length", str(500 * 1024 * 1024).encode())]
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):
            for path, method in (("/donors/upload", "POST"), ("/inventory", "GET"), ("/inventory/upload", "GET"), ("/auth/login", "POST")):
                inner = reading_app()
                sent = []

                async def send(m):
                    sent.append(m)

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                asyncio.run(self.build(inner)({"type": "http", "method": method, "path": path, "headers": huge}, receive, send))
                self.assertEqual(inner.calls["n"], 1, (path, method))

    def test_a_malformed_content_length_is_left_to_the_server(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):
            inner = reading_app()
            status, _, _ = asyncio.run(run_through(self.build(inner), [(b"content-length", b"abc")], [b"x" * 10]))
        self.assertEqual(status, 200)


class ThroughTheRealAppTests(unittest.TestCase):
    def setUp(self):
        app._active_account_until.clear()
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"lim-{self.tag}@example.com", "f": self.bank}).scalar()
        self.addCleanup(self.clear_uploads)
        token = auth.create_access_token(user_id=uid, email=f"lim-{self.tag}@example.com", facility_id=self.bank, role="staff")
        self.auth = [("authorization", f"Bearer {token}")]

    def clear_uploads(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            for t in ("blood_units", "inventory_snapshot_write_log", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.bank})

    def post(self, path, data, name="u.csv"):
        body, ctype = multipart(name, data)
        return asyncio.run(asgi("POST", path, [*self.auth, ("content-type", ctype)], body))

    def rows(self, n):
        col = TODAY - timedelta(days=2)
        return CSV_HEADER + "\n".join(f"{self.tag}-{i},O+,Packed RBC,Fridge A,450,{col},{TODAY + timedelta(days=20)}" for i in range(n))

    def test_a_big_upload_gets_a_413_in_plain_language_from_both_endpoints(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "2000"}):
            for path in ("/inventory/upload", "/forecast/historical-upload"):
                status, body = self.post(path, "x" * 150_000)   # past the limit plus the multipart allowance: refused by the middleware
                self.assertEqual(status, 413, path)
                self.assertIn("too large", json.loads(body)["detail"])

    def test_a_file_just_over_the_limit_but_inside_the_multipart_allowance_is_refused_by_the_handler(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "2000"}):
            status, body = self.post("/inventory/upload", self.rows(40))   # about 4 KB: the middleware lets it in, the exact check does not
        self.assertEqual(status, 413)
        self.assertIn("too large", json.loads(body)["detail"])

    def test_a_file_under_the_limit_is_accepted_with_the_same_response_as_before(self):
        status, body = self.post("/inventory/upload", self.rows(3))
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"rows_processed": 3, "errors": []})

    def test_a_renamed_spreadsheet_is_refused_with_a_clear_400(self):
        status, body = self.post("/inventory/upload", "PK\x03\x04" + "x" * 100)
        self.assertEqual(status, 400)
        self.assertIn("not a plain-text CSV", json.loads(body)["detail"])

    def test_the_donor_upload_still_has_no_size_check_of_ours(self):
        import inspect
        source = inspect.getsource(app.upload_donors)
        self.assertNotIn("upload_limits", source)
        self.assertTrue(inspect.iscoroutinefunction(app.upload_donors))
