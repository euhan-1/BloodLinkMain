"""Consent to the Privacy Notice at registration (RA 10173): enforced by the server, recorded with a version and a time, copied to the
account at approval, and tolerant of the migration not having been applied yet.

DB-backed (the local scratch database only: conftest refuses anything else); route handlers called directly, email mocked.
The migration test runs server/migrate_add_privacy_consent.sql against that scratch database to prove it is idempotent; nothing runs
it anywhere else.
"""

import contextlib
import io
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

import main
from database import engine

MIGRATION = Path(__file__).resolve().parent.parent / "migrate_add_privacy_consent.sql"
CONSENT_COLUMNS = ("privacy_consent_version", "privacy_consented_at")


class FakeRequest:
    class _Client:
        host = "203.0.113.7"

    def __init__(self):
        self.headers = {}
        self.client = self._Client()


def apply_migration():
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text(encoding="utf-8"))


def consent_column_count() -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND column_name = ANY(:c) "
            "AND table_name IN ('users', 'facility_registration_requests')"), {"c": list(CONSENT_COLUMNS)}).scalar()


class PrivacyConsentTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"consent-{self.tag}@example.com"
        self.name = f"Consent Facility {self.tag}"
        self.assertEqual(consent_column_count(), 4, "the scratch schema should carry the consent columns")
        with engine.begin() as conn:
            self.admin_id = conn.execute(text(
                "INSERT INTO users (email, password_hash, role) VALUES ('test-admin@example.com', 'x', 'admin') "
                "ON CONFLICT (email) DO UPDATE SET role = 'admin' RETURNING id")).scalar()
        self.addCleanup(self.clean)
        self.clear_limits()

    def clear_limits(self):
        for key in main._attempt_keys(FakeRequest(), self.email):
            main._FORGOT_LIMITER.clear(key)

    def clean(self):
        self.clear_limits()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM otp_codes WHERE registration_request_id IN (SELECT id FROM facility_registration_requests WHERE email = :e)"), {"e": self.email})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": self.email})
            conn.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id IN (SELECT id FROM facilities WHERE name = :n)"), {"n": self.name})
            conn.execute(text("DELETE FROM facilities WHERE name = :n"), {"n": self.name})
            conn.execute(text("DELETE FROM facility_registration_requests WHERE email = :e"), {"e": self.email})

    def body(self, **over):
        fields = dict(facility_name=self.name, facility_type="hospital", address="1 Test St", latitude=14.5, longitude=121.0,
                      contact_person="Juan Dela Cruz", email=self.email, phone="09170000000", password="Sup3rSecret!",
                      privacy_consent=True, privacy_notice_version=main.PRIVACY_NOTICE_VERSION)
        fields.update(over)
        return main.RegisterFacilityBody(**fields)

    def register(self, **over):
        with patch.object(main, "_send_registration_otp_email") as sent:
            main.register_facility(self.body(**over), FakeRequest())
        return sent

    def rows(self, sql="SELECT id, privacy_consent_version, privacy_consented_at FROM facility_registration_requests WHERE email = :e"):
        with engine.connect() as conn:
            return conn.execute(text(sql), {"e": self.email}).all()

    def approve(self):
        registration_id = self.rows("SELECT id FROM facility_registration_requests WHERE email = :e")[0][0]
        with engine.begin() as conn:
            conn.execute(text("UPDATE facility_registration_requests SET status = 'email_verified' WHERE id = :i"), {"i": registration_id})
        with patch.object(main, "_send_registration_approved_email"):
            return main.admin_approve_registration(registration_id, admin={"sub": str(self.admin_id)})

    # ── refused, and nothing is written ─────────────────────────────────────────────────────────────────────────
    def assert_refused(self, status_text, **over):
        with patch.object(main, "_send_registration_otp_email") as sent:
            with self.assertRaises(HTTPException) as caught:
                main.register_facility(self.body(**over), FakeRequest())
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn(status_text, caught.exception.detail)
        self.assertEqual(self.rows(), [], "a refused registration stores nothing")
        sent.assert_not_called()

    def test_a_registration_that_does_not_send_consent_is_refused(self):
        # the model default is "not given": a client that predates the consent box, or a script, gets the 400
        fields = dict(facility_name=self.name, facility_type="hospital", address="1 Test St", latitude=14.5, longitude=121.0,
                      contact_person="Juan", email=self.email, phone="09170000000", password="Sup3rSecret!")
        body = main.RegisterFacilityBody(**fields)
        self.assertIs(body.privacy_consent, False)
        with patch.object(main, "_send_registration_otp_email") as sent:
            with self.assertRaises(HTTPException) as caught:
                main.register_facility(body, FakeRequest())
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tick the consent box", caught.exception.detail)
        self.assertEqual(self.rows(), [])
        sent.assert_not_called()

    def test_consent_false_is_refused(self):
        self.assert_refused("tick the consent box", privacy_consent=False)

    def test_only_a_real_true_counts_as_consent(self):
        for sloppy in ("yes", "true", "on", 1, "1"):
            with self.assertRaises(ValidationError, msg=repr(sloppy)):
                self.body(privacy_consent=sloppy)

    def test_consent_to_a_version_the_server_does_not_know_is_refused(self):
        self.assert_refused("has changed", privacy_notice_version="0.0-not-a-version")
        self.assert_refused("has changed", privacy_notice_version="")

    def test_a_refused_registration_does_not_use_up_the_email_budget(self):
        keys = main._attempt_keys(FakeRequest(), self.email)
        for _ in range(12):
            with self.assertRaises(HTTPException):
                main.register_facility(self.body(privacy_consent=False), FakeRequest())
        self.assertEqual([main._FORGOT_LIMITER.retry_after(k) for k in keys], [0] * len(keys))

    # ── recorded ──────────────────────────────────────────────────────────────────────────────────────────────────
    def test_the_version_and_the_time_are_recorded_with_the_registration(self):
        before = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.register()
        [(rid, version, at)] = self.rows()
        self.assertEqual(version, main.PRIVACY_NOTICE_VERSION)
        self.assertGreaterEqual(at, before)
        self.assertLessEqual(at, datetime.now(timezone.utc) + timedelta(seconds=5))

    def test_a_resubmission_before_verification_records_the_consent_again(self):
        self.register()
        with engine.begin() as conn:  # pretend the first consent was given long ago, under an older notice
            conn.execute(text("UPDATE facility_registration_requests SET privacy_consent_version = 'old', privacy_consented_at = :t WHERE email = :e"),
                         {"t": datetime(2026, 1, 1, tzinfo=timezone.utc), "e": self.email})
        self.clear_limits()
        self.register()
        [(rid, version, at)] = self.rows()
        self.assertEqual(version, main.PRIVACY_NOTICE_VERSION)
        self.assertGreater(at, datetime(2026, 6, 1, tzinfo=timezone.utc))

    def test_approval_copies_the_consent_to_the_account(self):
        self.register()
        [(rid, version, at)] = self.rows()
        self.approve()
        self.assertEqual(self.rows("SELECT privacy_consent_version, privacy_consented_at FROM users WHERE email = :e"), [(version, at)])

    def test_a_registration_with_no_consent_on_file_gives_an_account_with_none(self):
        # made before the consent existed, or while the migration was missing: NULL = not recorded
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO facility_registration_requests (facility_name, facility_type, address, latitude, longitude, contact_person, email, phone, password_hash, status) "
                "VALUES (:n, 'hospital', '1 Test St', 14.5, 121.0, 'Juan', :e, '09170000000', 'x', 'email_verified')"), {"n": self.name, "e": self.email})
        self.approve()
        self.assertEqual(self.rows("SELECT privacy_consent_version, privacy_consented_at FROM users WHERE email = :e"), [(None, None)])

    # ── the migration ────────────────────────────────────────────────────────────────────────────────────────────
    def test_the_migration_is_idempotent_and_changes_only_these_four_columns(self):
        with engine.connect() as conn:
            columns_before = conn.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'")).scalar()
        apply_migration()
        apply_migration()  # a second run must be a no-op, not an error
        with engine.connect() as conn:
            columns_after = conn.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'")).scalar()
        self.assertEqual(columns_after, columns_before)
        self.assertEqual(consent_column_count(), 4)
        sql = MIGRATION.read_text(encoding="utf-8")
        statements = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).upper()
        for forbidden in ("DROP ", "DELETE ", "UPDATE ", "TRUNCATE ", "INSERT "):
            self.assertNotIn(forbidden, statements, "the migration only adds columns")
        self.assertEqual(statements.count("ADD COLUMN IF NOT EXISTS"), 4)

    def test_before_the_migration_is_applied_registration_and_approval_still_work_and_say_so(self):
        self.addCleanup(self.reset_after_dropping_columns)
        with engine.begin() as conn:
            for table in ("users", "facility_registration_requests"):
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS privacy_consent_version, DROP COLUMN IF EXISTS privacy_consented_at"))
        main._consent_columns_seen = False
        self.assertEqual(consent_column_count(), 0)

        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.register()
            self.approve()
        self.assertIn("[consent]", log.getvalue())
        self.assertIn("NOT being recorded", log.getvalue())
        self.assertEqual(self.rows("SELECT count(*) FROM users WHERE email = :e")[0][0], 1, "the account was still created")

        # and the same process picks the columns up as soon as the migration has been applied, without a restart
        apply_migration()
        self.clean()
        self.clear_limits()
        self.register()
        self.assertEqual(self.rows()[0][1], main.PRIVACY_NOTICE_VERSION)

    def reset_after_dropping_columns(self):
        apply_migration()  # leaves the scratch schema as the rest of the suite expects it
        main._consent_columns_seen = False
        self.assertEqual(consent_column_count(), 4)

    def test_the_notice_version_is_a_non_empty_string(self):
        self.assertIsInstance(main.PRIVACY_NOTICE_VERSION, str)
        self.assertTrue(main.PRIVACY_NOTICE_VERSION.strip())
