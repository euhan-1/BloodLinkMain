"""What an anonymous visitor can reach: /health says nothing but ok or 503, the API docs are off unless ENABLE_API_DOCS, and
GET /facilities needs a login. Needs the scratch database (conftest.py)."""
import asyncio
import io
import json
import os
import unittest
import uuid
from contextlib import redirect_stdout
from unittest import mock

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import auth
import main as app
from database import engine
from tests.test_upload_endpoints import asgi

SECRET_DETAIL = 'connection to server at "db.internal-secret-host.example" (10.1.2.3), port 5432 failed: FATAL: password authentication failed for user "postgres.abcdefgh"'


class HealthTests(unittest.TestCase):
    def test_success_is_just_ok(self):
        self.assertEqual(app.health(), {"status": "ok"})

    def test_failure_is_a_bare_503_and_the_real_error_goes_to_the_log(self):
        broken = mock.MagicMock()
        broken.connect.side_effect = OperationalError("SELECT 1", {}, Exception(SECRET_DETAIL))
        log = io.StringIO()
        with mock.patch.object(app, "engine", broken), redirect_stdout(log):
            response = app.health()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.body, b"")                       # no detail at all
        self.assertNotIn("secret-host", response.body.decode())
        self.assertIn("[health] database check failed", log.getvalue())
        self.assertIn("internal-secret-host", log.getvalue())      # ...but the operator can still see why

    def test_through_the_real_app_a_failure_has_no_body_and_no_detail(self):
        broken = mock.MagicMock()
        broken.connect.side_effect = OperationalError("SELECT 1", {}, Exception(SECRET_DETAIL))
        with mock.patch.object(app, "engine", broken), redirect_stdout(io.StringIO()):
            status, body = asyncio.run(asgi("GET", "/health"))
        self.assertEqual((status, body), (503, b""))

    def test_the_health_response_no_longer_lists_the_cors_origins(self):
        self.assertNotIn("cors_allowed_origins", app.health())


class ApiDocsTests(unittest.TestCase):
    def test_the_switch_defaults_to_off_and_only_an_explicit_true_turns_it_on(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertFalse(app.api_docs_enabled())
        for value, expected in (("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True), ("false", False), ("", False), ("0", False)):
            with mock.patch.dict("os.environ", {"ENABLE_API_DOCS": value}):
                self.assertEqual(app.api_docs_enabled(), expected, value)

    def test_the_settings_remove_every_docs_route_or_leave_the_defaults(self):
        self.assertEqual(app.fastapi_docs_settings(False), {"docs_url": None, "redoc_url": None, "openapi_url": None})
        self.assertEqual(app.fastapi_docs_settings(True), {})
        off, on = FastAPI(**app.fastapi_docs_settings(False)), FastAPI(**app.fastapi_docs_settings(True))
        self.assertEqual((off.openapi_url, off.docs_url, off.redoc_url), (None, None, None))
        self.assertEqual((on.openapi_url, on.docs_url, on.redoc_url), ("/openapi.json", "/docs", "/redoc"))

    @unittest.skipIf(app.api_docs_enabled(), "ENABLE_API_DOCS is set in this environment, so the app was built with docs on")
    def test_the_real_app_serves_no_docs_and_no_schema(self):
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            status, _ = asyncio.run(asgi("GET", path))
            self.assertEqual(status, 404, path)


class FacilitiesListTests(unittest.TestCase):
    def setUp(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"PublicList {uuid.uuid4().hex[:6]}"}).scalar()
            self.uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                 {"e": f"pub-{uuid.uuid4().hex[:8]}@example.com", "f": self.fid}).scalar()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.uid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def test_it_needs_a_login(self):
        for headers in ([], [("authorization", "Bearer not-a-token")]):
            status, _ = asyncio.run(asgi("GET", "/facilities", headers))
            self.assertEqual(status, 401)

    def test_with_a_login_it_lists_the_active_facilities_as_before(self):
        token = auth.create_access_token(user_id=self.uid, email="x@example.com", facility_id=self.fid, role="staff")
        status, body = asyncio.run(asgi("GET", "/facilities", [("authorization", f"Bearer {token}")]))
        self.assertEqual(status, 200)
        rows = json.loads(body)
        self.assertIn(self.fid, [r["id"] for r in rows])
        self.assertEqual(set(rows[0]), {"id", "name", "facility_type"})

    def test_the_registration_and_geocode_routes_a_visitor_needs_are_still_public(self):
        # not a call (that would send mail or reach Nominatim): the routes exist and take no login dependency
        from fastapi.routing import APIRoute
        needs_login = {}
        for route in app.app.routes:
            if isinstance(route, APIRoute) and route.path in ("/facilities/register", "/facilities/register/verify", "/facilities/geocode",
                                                             "/auth/login", "/health"):
                needs_login[route.path] = any(d.call in (app.get_current_user, app.get_acting_facility_id)
                                              for d in route.dependant.dependencies)
        self.assertEqual(needs_login, {p: False for p in ("/facilities/register", "/facilities/register/verify", "/facilities/geocode",
                                                         "/auth/login", "/health")})
