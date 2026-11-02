"""BloodLink does not collect, display or store a DOH licence number: it does not verify them, so it must not ask for them.

What these tests prove: the scratch schema (built from tests/schema.sql) has no such column; registration, profile completion, the profile
read, the admin queue, approval and the request/receipt facility blocks all work without it and never return it; a client that still
sends the field (a browser tab opened before the deploy) is accepted and the value is dropped, not stored; and neither the server nor
the frontend source names the field any more. What they do not prove: the production DROP COLUMN, which is a separate step.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import pathlib
import unittest
import uuid
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import q
from tests.test_facility_registration import FakeRequest
from tests.test_request_transitions import TransitionBase

REPO = pathlib.Path(__file__).resolve().parents[2]
GONE = "doh_license"


class SchemaAndSourceTests(unittest.TestCase):
    def test_no_table_in_the_scratch_schema_has_the_column(self):
        self.assertEqual(q("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'doh_license_number'"), [])

    def test_the_server_source_does_not_name_it(self):
        for p in sorted((REPO / "server").glob("*.py")):
            self.assertNotIn(GONE, p.read_text(encoding="utf-8"), p.name)

    def test_the_frontend_source_does_not_name_it(self):
        for p in sorted((REPO / "src").rglob("*.ts*")):
            self.assertNotIn(GONE, p.read_text(encoding="utf-8"), str(p.relative_to(REPO)))


class ProfileTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'hospital', true, false) RETURNING id"), {"n": f"NoDoh {uuid.uuid4().hex[:6]}"}).scalar()
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def body(self, **extra):
        return app.CompleteProfileBody(address="1 Test St", latitude=14.5, longitude=121.0, department="Blood Services Unit", **extra)

    def test_a_profile_completes_without_it_and_nothing_returns_it(self):
        saved = app.complete_facility_profile(self.body(), facility_id=self.fid)
        self.assertEqual(set(saved), {"id", "name", "facility_type", "address", "latitude", "longitude", "department", "profile_completed"})
        self.assertTrue(saved["profile_completed"])
        read = app.get_my_facility(facility_id=self.fid)
        self.assertEqual(set(read), set(saved))
        self.assertNotIn(GONE, " ".join(read))

    def test_a_client_that_still_sends_it_is_accepted_and_the_value_goes_nowhere(self):
        body = self.body(doh_license_number="DOH-OLD-TAB-001")
        self.assertFalse(hasattr(body, "doh_license_number"))
        saved = app.complete_facility_profile(body, facility_id=self.fid)
        self.assertNotIn("DOH-OLD-TAB-001", repr(saved) + repr(app.get_my_facility(facility_id=self.fid)))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.email = f"nodoh-{uuid.uuid4().hex[:8]}@example.com"
        app._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            ids = [r[0] for r in c.execute(text("SELECT id FROM facility_registration_requests WHERE email = :e"), {"e": self.email})]
            c.execute(text("DELETE FROM otp_codes WHERE registration_request_id = ANY(:i)"), {"i": ids})
            c.execute(text("DELETE FROM facility_registration_requests WHERE email = :e"), {"e": self.email})
        app._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")

    def fields(self, **extra):
        return dict(facility_name="No Licence Hospital", facility_type="hospital", address="1 Test St", latitude=14.5, longitude=121.0,
                    contact_person="Juan Dela Cruz", email=self.email, phone="09170000000", password="Sup3rSecret!", **extra)

    def register(self, **extra):
        with mock.patch.object(app, "_send_registration_otp_email"):
            return app.register_facility(app.RegisterFacilityBody(**self.fields(**extra)), FakeRequest())

    def test_a_registration_is_stored_without_it(self):
        self.register()
        rows = q("SELECT facility_name, address FROM facility_registration_requests WHERE email = :e", e=self.email)
        self.assertEqual(rows, [("No Licence Hospital", "1 Test St")])

    def test_a_client_that_still_sends_it_registers_and_the_value_is_dropped(self):
        body = app.RegisterFacilityBody(**self.fields(doh_license_number="DOH-OLD-TAB-002"))
        self.assertFalse(hasattr(body, "doh_license_number"))
        self.register(doh_license_number="DOH-OLD-TAB-002")
        self.assertEqual(q("SELECT count(*) FROM facility_registration_requests WHERE email = :e", e=self.email)[0][0], 1)

    def test_the_admin_queue_does_not_list_it(self):
        self.register()
        with engine.begin() as c:
            c.execute(text("UPDATE facility_registration_requests SET status = 'email_verified' WHERE email = :e"), {"e": self.email})
        mine = [r for r in app.admin_list_registrations() if r["email"] == self.email]
        self.assertEqual(len(mine), 1)
        self.assertEqual(set(mine[0]), {"id", "facility_name", "facility_type", "address", "latitude", "longitude", "contact_person", "email", "phone", "created_at"})


class RequestFacilityBlockTests(TransitionBase):
    def test_a_request_and_its_receipt_carry_each_facility_without_it(self):
        rid = self.new_request()
        detail = app.get_request_detail(rid, facility_id=self.bank)
        for side in ("requesting_facility", "supplying_facility"):
            self.assertEqual(set(detail["request"][side]), {"id", "name", "facility_type", "address"}, side)
