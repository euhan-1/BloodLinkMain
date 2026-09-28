"""POST /facilities/register + GET /facilities/geocode.

email_service.send_email is mocked everywhere here (unittest.mock, stdlib —
no new dependency) rather than hit live: Resend's sandbox sender can only
actually deliver to the account's own registered address, so a real call
here would either silently fail against example.com recipients or spend
real send quota for no signal: what matters for these tests is that the
right email (OTP vs. existing-account notice) gets requested with the right
recipient, not that Resend itself works — that's email_service's own
concern, not this endpoint's.

DB-backed otherwise (real facility_registration_requests / otp_codes /
users rows, live DB per this project's convention), same style as
test_admin_create_account_for_facility.py.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_facility_registration -v
"""

import unittest
import uuid
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import text

import auth
import geocode_service
import main
from database import engine


class PasswordPolicyTests(unittest.TestCase):
    """Pure — no DB."""

    def test_too_short_rejected(self):
        self.assertIsNotNone(auth.validate_registration_password("Abcd123"))

    def test_all_letters_rejected(self):
        self.assertIsNotNone(auth.validate_registration_password("abcdefghijkl"))

    def test_all_digits_rejected(self):
        self.assertIsNotNone(auth.validate_registration_password("123456789012"))

    def test_common_password_rejected(self):
        self.assertIsNotNone(auth.validate_registration_password("password123"))

    def test_reasonable_password_accepted(self):
        self.assertIsNone(auth.validate_registration_password("Batangas2026Hospital"))


class GeocodeAddressTests(unittest.TestCase):
    """Hits the live Nominatim service — same convention as this suite's
    other network/DB-backed tests. One well-known address, one garbage one."""

    def test_known_address_returns_coordinates(self):
        result = geocode_service.geocode_address("Manila, Philippines")
        self.assertIsNotNone(result)
        lat, lon = result
        self.assertTrue(-90 <= lat <= 90)
        self.assertTrue(-180 <= lon <= 180)

    def test_garbage_address_returns_none(self):
        self.assertIsNone(geocode_service.geocode_address("zzqxvv nonsense not a place 999999"))

    def test_blank_address_returns_none(self):
        self.assertIsNone(geocode_service.geocode_address("   "))


class RegisterFacilityBody:
    """Stand-in for main.RegisterFacilityBody, same convention as this
    suite's other endpoint tests — real pydantic validation is exercised
    separately in RegisterFacilityBodyValidationTests below."""
    def __init__(self, **kwargs):
        defaults = dict(
            facility_name="Test Facility", facility_type="hospital", address="123 Test St",
            latitude=14.5, longitude=121.0, doh_license_number="DOH-TEST-001",
            contact_person="Juan Dela Cruz", email="", phone="09170000000",
            password="Sup3rSecret!",
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


class FakeRequest:
    """Minimal stand-in for FastAPI's Request — only what _attempt_key reads."""
    class _Client:
        host = "203.0.113.1"

    def __init__(self):
        self.headers = {}
        self.client = self._Client()


class RegisterFacilityBodyValidationTests(unittest.TestCase):
    """Real pydantic model — confirms the field_validators actually reject
    what they're supposed to."""

    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            facility_name="Test Facility", facility_type="hospital", address="123 Test St",
            latitude=14.5, longitude=121.0, doh_license_number="DOH-TEST-001",
            contact_person="Juan Dela Cruz", email="test@example.com", phone="09170000000",
            password="Sup3rSecret!",
        )
        kwargs.update(overrides)
        return kwargs

    def test_valid_body_accepted(self):
        main.RegisterFacilityBody(**self._valid_kwargs())

    def test_weak_password_rejected(self):
        with self.assertRaises(ValidationError):
            main.RegisterFacilityBody(**self._valid_kwargs(password="weak"))

    def test_invalid_facility_type_rejected(self):
        with self.assertRaises(ValidationError):
            main.RegisterFacilityBody(**self._valid_kwargs(facility_type="clinic"))

    def test_out_of_range_latitude_rejected(self):
        with self.assertRaises(ValidationError):
            main.RegisterFacilityBody(**self._valid_kwargs(latitude=999))

    def test_blank_facility_name_rejected(self):
        with self.assertRaises(ValidationError):
            main.RegisterFacilityBody(**self._valid_kwargs(facility_name="   "))


class RegisterFacilityEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"registrant-{self.tag}@example.com"
        main._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")

    def tearDown(self):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM otp_codes WHERE registration_request_id IN "
                    "(SELECT id FROM facility_registration_requests WHERE email = :email)"
                ),
                {"email": self.email},
            )
            conn.execute(text("DELETE FROM facility_registration_requests WHERE email = :email"), {"email": self.email})
            conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": self.email})
        main._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")

    @patch("main.email_service.send_email")
    def test_creates_pending_row_and_sends_otp(self, mock_send):
        result = main.register_facility(RegisterFacilityBody(email=self.email), FakeRequest())
        self.assertEqual(result, main._REGISTRATION_RESPONSE)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, password_hash FROM facility_registration_requests WHERE email = :email"),
                {"email": self.email},
            ).mappings().first()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "submitted")
        self.assertNotEqual(row["password_hash"], "Sup3rSecret!")
        self.assertTrue(auth.verify_password("Sup3rSecret!", row["password_hash"]))

        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to"], self.email)
        self.assertIn("Verify", mock_send.call_args.kwargs["subject"])

    @patch("main.email_service.send_email")
    def test_resubmission_before_verification_updates_same_row(self, mock_send):
        main.register_facility(RegisterFacilityBody(email=self.email, facility_name="First Name"), FakeRequest())
        main._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")
        main.register_facility(RegisterFacilityBody(email=self.email, facility_name="Corrected Name"), FakeRequest())

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT facility_name FROM facility_registration_requests WHERE email = :email"),
                {"email": self.email},
            ).mappings().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["facility_name"], "Corrected Name")

    @patch("main.email_service.send_email")
    def test_resubmission_invalidates_prior_otp_code(self, mock_send):
        main.register_facility(RegisterFacilityBody(email=self.email), FakeRequest())
        with engine.connect() as conn:
            registration_id = conn.execute(
                text("SELECT id FROM facility_registration_requests WHERE email = :email"), {"email": self.email}
            ).scalar()
        first_html = mock_send.call_args.kwargs["html"]

        main._FORGOT_LIMITER.clear(f"203.0.113.1|{self.email}")
        main.register_facility(RegisterFacilityBody(email=self.email), FakeRequest())
        second_html = mock_send.call_args.kwargs["html"]
        self.assertNotEqual(first_html, second_html)

        with engine.connect() as conn:
            live_codes = conn.execute(
                text("SELECT COUNT(*) FROM otp_codes WHERE registration_request_id = :rid AND used_at IS NULL"),
                {"rid": registration_id},
            ).scalar()
        self.assertEqual(live_codes, 1)

    @patch("main.email_service.send_email")
    def test_existing_account_email_gets_notice_not_a_pending_row(self, mock_send):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (email, password_hash, role) VALUES (:email, 'x', 'staff')"),
                {"email": self.email},
            )

        result = main.register_facility(RegisterFacilityBody(email=self.email), FakeRequest())
        self.assertEqual(result, main._REGISTRATION_RESPONSE)

        with engine.connect() as conn:
            pending = conn.execute(
                text("SELECT COUNT(*) FROM facility_registration_requests WHERE email = :email"), {"email": self.email}
            ).scalar()
        self.assertEqual(pending, 0)

        mock_send.assert_called_once()
        self.assertIn("already", mock_send.call_args.kwargs["subject"].lower())

    @patch("main.email_service.send_email")
    def test_email_send_failure_does_not_fail_the_request(self, mock_send):
        mock_send.side_effect = main.email_service.EmailSendError("Resend unreachable")
        result = main.register_facility(RegisterFacilityBody(email=self.email), FakeRequest())
        self.assertEqual(result, main._REGISTRATION_RESPONSE)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM facility_registration_requests WHERE email = :email"), {"email": self.email}
            ).mappings().first()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
