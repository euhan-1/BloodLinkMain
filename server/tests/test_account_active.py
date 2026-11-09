"""A signed token no longer outlives its account: every authenticated request checks that the user still exists and that its facility is
still active. The pass is remembered for at most 60 seconds, a rejection never is, and deactivating through the admin endpoint
clears it. Needs the scratch database (conftest.py)."""
import unittest
import uuid
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import event, text

import auth
import main as app
from database import engine


def bearer(user_id, facility_id, role="staff"):
    return "Bearer " + auth.create_access_token(user_id=user_id, email="x@example.com", facility_id=facility_id, role=role)


class AccountActiveTests(unittest.TestCase):
    def setUp(self):
        app._active_account_until.clear()
        self.tag = uuid.uuid4().hex[:8]
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Active {self.tag}"}).scalar()
            self.uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                 {"e": f"active-{self.tag}@example.com", "f": self.fid}).scalar()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE facility_id = :f OR id = :u"), {"f": self.fid, "u": self.uid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def acting(self):
        return app.get_acting_facility_id(authorization=bearer(self.uid, self.fid), x_dev_facility_id=None)

    def set_active(self, value):
        with engine.begin() as c:
            c.execute(text("UPDATE facilities SET is_active = :a WHERE id = :f"), {"a": value, "f": self.fid})

    def statements(self, fn):
        seen = []
        record = lambda *a: seen.append(a[2])  # noqa: E731
        event.listen(engine, "before_cursor_execute", record)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return seen

    def test_an_active_account_passes(self):
        self.assertEqual(self.acting(), self.fid)

    def test_a_deactivated_facility_is_refused_on_the_next_request_after_the_cache_is_cleared(self):
        self.assertEqual(self.acting(), self.fid)
        self.set_active(False)
        app._active_account_until.clear()
        with self.assertRaises(HTTPException) as ctx:
            self.acting()
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("deactivated", ctx.exception.detail)

    def test_the_admin_deactivation_takes_effect_at_once_even_inside_the_cache_window(self):
        self.assertEqual(self.acting(), self.fid)            # a pass is now remembered
        app.admin_update_facility_status(self.fid, app.UpdateFacilityStatusBody(is_active=False))
        with self.assertRaises(HTTPException) as ctx:
            self.acting()                                     # no waiting for the 30 seconds to run out
        self.assertEqual(ctx.exception.status_code, 403)

    def test_reactivation_works_at_once_because_a_rejection_is_never_remembered(self):
        self.set_active(False)
        for _ in range(2):
            with self.assertRaises(HTTPException):
                self.acting()
        self.set_active(True)
        self.assertEqual(self.acting(), self.fid)

    def test_a_deleted_user_is_refused(self):
        self.assertEqual(self.acting(), self.fid)
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.uid})
        with self.assertRaises(HTTPException) as ctx:
            self.acting()
        self.assertEqual(ctx.exception.status_code, 401)

    def test_the_other_dependencies_check_it_too(self):
        self.set_active(False)
        with self.assertRaises(HTTPException) as ctx:
            app.get_current_user(authorization=bearer(self.uid, self.fid))   # what /auth/me, /facilities and update-password use
        self.assertEqual(ctx.exception.status_code, 403)

    def test_a_pass_is_checked_once_then_remembered_then_checked_again_after_the_window(self):
        self.assertEqual(len(self.statements(self.acting)), 1)    # the lookup
        self.assertEqual(len(self.statements(self.acting)), 0)    # remembered: no query at all
        until = max(app._active_account_until.values())  # when the remembered pass runs out
        with mock.patch.object(app.time, "monotonic", return_value=until - 1):
            self.assertEqual(len(self.statements(self.acting)), 0)  # still inside the window
        with mock.patch.object(app.time, "monotonic", return_value=until + 1):
            self.assertEqual(len(self.statements(self.acting)), 1)  # the window is over: looked up again

    def test_the_window_is_never_longer_than_sixty_seconds(self):
        self.assertLessEqual(app.AUTH_ACTIVE_CACHE_SECONDS, 60)

    def test_the_dev_override_still_skips_the_check_it_always_skipped(self):
        with mock.patch.object(app, "ALLOW_DEV_FACILITY_OVERRIDE", True):
            self.assertEqual(app.get_acting_facility_id(authorization=None, x_dev_facility_id=self.fid), self.fid)


class AdminAccountTests(unittest.TestCase):
    def setUp(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            self.uid = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', 'admin') RETURNING id"),
                                 {"e": f"admin-{uuid.uuid4().hex[:8]}@example.com"}).scalar()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.uid})

    def test_an_admin_with_no_facility_passes_and_a_deleted_admin_does_not(self):
        token = bearer(self.uid, None, role="admin")
        self.assertEqual(app.require_admin_role(app.get_current_user(authorization=token))["role"], "admin")
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.uid})
        with self.assertRaises(HTTPException) as ctx:
            app.get_current_user(authorization=token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_an_admin_token_is_still_refused_by_the_facility_endpoints(self):
        with self.assertRaises(HTTPException) as ctx:
            app.get_acting_facility_id(authorization=bearer(self.uid, None, role="admin"), x_dev_facility_id=None)
        self.assertEqual(ctx.exception.status_code, 403)
