"""POST /facilities/register/verify — gates
facility_registration_requests.status: submitted -> email_verified.

DB-backed, real rows created directly (bypassing register_facility so each
test controls its own OTP code precisely), same convention as this suite's
other endpoint tests.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_facility_registration_verify -v
"""

import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy import text

import auth
import main
from database import engine


class VerifyFacilityRegistrationEmailTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"verify-test-{self.tag}@example.com"
        with engine.begin() as conn:
            self.registration_id = conn.execute(
                text(
                    """
                    INSERT INTO facility_registration_requests
                        (facility_name, facility_type, address, doh_license_number, contact_person, email, phone, password_hash)
                    VALUES ('Test Facility', 'hospital', '123 Test St', 'DOH-TEST', 'Test Person', :email, '09170000000', 'x')
                    RETURNING id
                    """
                ),
                {"email": self.email},
            ).scalar()

    def tearDown(self):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM otp_codes WHERE registration_request_id = :id"), {"id": self.registration_id})
            conn.execute(text("DELETE FROM facility_registration_requests WHERE id = :id"), {"id": self.registration_id})

    def _issue_code(self) -> str:
        with engine.begin() as conn:
            return main._issue_otp(
                conn, "registration_email_verify", self.email, registration_request_id=self.registration_id
            )

    def _status(self) -> str:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT status FROM facility_registration_requests WHERE id = :id"), {"id": self.registration_id}
            ).scalar()

    def test_correct_code_flips_status_to_email_verified(self):
        code = self._issue_code()
        result = main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code=code))
        self.assertEqual(result, main._VERIFICATION_SUCCESS_RESPONSE)
        self.assertEqual(self._status(), "email_verified")

    def test_wrong_code_rejected_and_status_unchanged(self):
        self._issue_code()
        with self.assertRaises(HTTPException) as ctx:
            main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code="000000"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._status(), "submitted")

    def test_unknown_email_gets_same_generic_error(self):
        with self.assertRaises(HTTPException) as ctx:
            main.verify_facility_registration_email(
                main.VerifyRegistrationEmailBody(email="nobody-registered@example.com", code="123456")
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, main._VERIFICATION_FAILED_DETAIL)

    def test_already_verified_is_idempotent(self):
        code = self._issue_code()
        main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code=code))
        # Second call with the same (now-used) code must still succeed, not error.
        result = main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code=code))
        self.assertEqual(result, main._VERIFICATION_SUCCESS_RESPONSE)

    def test_attempt_capped_code_is_rejected_even_when_correct(self):
        code = self._issue_code()
        for _ in range(auth.OTP_MAX_ATTEMPTS):
            with self.assertRaises(HTTPException):
                main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code="000000"))
        with self.assertRaises(HTTPException) as ctx:
            main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code=code))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._status(), "submitted")

    def test_archived_registration_not_found(self):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE facility_registration_requests SET archived_at = now() WHERE id = :id"),
                {"id": self.registration_id},
            )
        code = self._issue_code()
        with self.assertRaises(HTTPException) as ctx:
            main.verify_facility_registration_email(main.VerifyRegistrationEmailBody(email=self.email, code=code))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
