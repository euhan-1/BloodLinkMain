"""Tests for POST /admin/facilities/{facility_id}/accounts — provisions a
login for a facility that already exists but has none, closing the gap
POST /admin/facilities can't (that endpoint only ever creates a brand new
facility alongside its first account).

DB-backed, same convention as test_facility_thresholds.py and
test_expired_inventory.py: route handlers are called directly as plain
Python functions (Depends() only resolves via real HTTP request handling, so
a direct call just uses whatever's passed as an argument). Each test creates
its own disposable facility in setUp and deletes it in tearDown.

Not covered here: the dependencies=[Depends(require_admin_role)] gate on the
route decorator itself — that's FastAPI routing-layer enforcement, identical
to (and no more or less tested than) the other three /admin/* endpoints,
none of which have route-level auth tests in this suite either. What's
tested here is everything this endpoint's own body controls: the facility
must exist, one account per facility is enforced, and the shared
_create_staff_account path (also used by POST /admin/facilities) works.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_admin_create_account_for_facility -v
"""

import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy import text

import main
from database import engine


def _make_facility(conn, name: str, facility_type: str = "bloodbank") -> int:
    return conn.execute(
        text("INSERT INTO facilities (name, facility_type) VALUES (:n, :t) RETURNING id"),
        {"n": name, "t": facility_type},
    ).scalar()


def _cleanup_facility(conn, facility_id: int) -> None:
    conn.execute(text("DELETE FROM users WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM facilities WHERE id = :fid"), {"fid": facility_id})


class CreateAccountForFacilityBody:
    """Stand-in for main.CreateAccountForFacilityBody (a pydantic model) with
    plain attribute access — same convention test_facility_thresholds.py
    uses for UpdateThresholdBody."""
    def __init__(self, email: str):
        self.email = email


class CreateAccountForExistingFacilityTests(unittest.TestCase):
    def setUp(self):
        # A unique-per-run name/email so repeated test runs never collide on
        # the users.email uniqueness this endpoint itself relies on.
        self.tag = uuid.uuid4().hex[:8]
        with engine.begin() as conn:
            self.facility_id = _make_facility(conn, f"Test Facility {self.tag}")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_id)

    def test_creates_a_real_staff_account_with_a_temp_password(self):
        email = f"newaccount-{self.tag}@example.com"
        result = main.admin_create_account_for_facility(
            self.facility_id, CreateAccountForFacilityBody(email=email)
        )

        self.assertEqual(result["facility"]["id"], self.facility_id)
        self.assertEqual(result["user"]["email"], email)
        self.assertEqual(result["user"]["facility_id"], self.facility_id)
        self.assertEqual(result["user"]["role"], "staff")
        self.assertTrue(result["temporary_password"])

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT email, role, facility_id, must_change_password, password_hash FROM users WHERE facility_id = :fid"),
                {"fid": self.facility_id},
            ).mappings().first()
        self.assertIsNotNone(row)
        self.assertEqual(row["email"], email)
        self.assertEqual(row["role"], "staff")
        self.assertTrue(row["must_change_password"])
        # The returned temp password must actually verify against the stored
        # hash — proves this goes through the real bcrypt path
        # (auth.hash_password), not a placeholder.
        self.assertTrue(main.auth.verify_password(result["temporary_password"], row["password_hash"]))

    def test_refuses_a_facility_that_already_has_an_account(self):
        email = f"first-{self.tag}@example.com"
        main.admin_create_account_for_facility(self.facility_id, CreateAccountForFacilityBody(email=email))

        with self.assertRaises(HTTPException) as ctx:
            main.admin_create_account_for_facility(
                self.facility_id, CreateAccountForFacilityBody(email=f"second-{self.tag}@example.com")
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("already has an account", ctx.exception.detail)

        # The refusal must not have created a second row.
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE facility_id = :fid"), {"fid": self.facility_id}
            ).scalar()
        self.assertEqual(count, 1)

    def test_refuses_a_nonexistent_facility(self):
        with self.assertRaises(HTTPException) as ctx:
            main.admin_create_account_for_facility(
                999999999, CreateAccountForFacilityBody(email=f"nobody-{self.tag}@example.com")
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_refuses_an_email_already_used_by_another_account(self):
        with engine.begin() as conn:
            other_facility_id = _make_facility(conn, f"Other Facility {self.tag}")
        self.addCleanup(lambda: _cleanup_with_own_connection(other_facility_id))

        email = f"taken-{self.tag}@example.com"
        main.admin_create_account_for_facility(other_facility_id, CreateAccountForFacilityBody(email=email))

        with self.assertRaises(HTTPException) as ctx:
            main.admin_create_account_for_facility(self.facility_id, CreateAccountForFacilityBody(email=email))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("already exists", ctx.exception.detail)

        # This facility must still have zero accounts — the email conflict
        # must not have left a partial row behind.
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE facility_id = :fid"), {"fid": self.facility_id}
            ).scalar()
        self.assertEqual(count, 0)


def _cleanup_with_own_connection(facility_id: int) -> None:
    with engine.begin() as conn:
        _cleanup_facility(conn, facility_id)


if __name__ == "__main__":
    unittest.main()
