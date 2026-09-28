"""otp_codes: auth.generate_otp_code/hash_otp_code (pure) and
main._issue_otp/_verify_otp (DB-backed, shared by registration email
verification and password reset — see schema_otp_codes.sql).

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_otp -v
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import auth
import main
from database import engine


class OtpHelperTests(unittest.TestCase):
    """Pure — no DB."""

    def test_code_is_six_digits_zero_padded(self):
        for _ in range(50):
            code = auth.generate_otp_code()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

    def test_hash_is_deterministic_and_distinguishes_codes(self):
        self.assertEqual(auth.hash_otp_code("012345"), auth.hash_otp_code("012345"))
        self.assertNotEqual(auth.hash_otp_code("012345"), auth.hash_otp_code("012346"))

    def test_hash_is_not_the_raw_code(self):
        self.assertNotEqual(auth.hash_otp_code("012345"), "012345")


class OtpDbTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        with engine.begin() as conn:
            self.user_id = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES (:email, 'x', 'staff') RETURNING id"
                ),
                {"email": f"otp-test-{self.tag}@example.com"},
            ).scalar()

    def tearDown(self):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM otp_codes WHERE user_id = :uid"), {"uid": self.user_id})
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.user_id})

    def test_issue_then_verify_with_correct_code_succeeds(self):
        with engine.begin() as conn:
            code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
        with engine.begin() as conn:
            ok = main._verify_otp(conn, "password_reset", code, user_id=self.user_id)
        self.assertTrue(ok)

    def test_wrong_code_fails_and_is_single_use_afterward(self):
        with engine.begin() as conn:
            code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
        with engine.begin() as conn:
            self.assertFalse(main._verify_otp(conn, "password_reset", "000000", user_id=self.user_id))
        # The right code still works after one wrong guess (attempts < cap).
        with engine.begin() as conn:
            self.assertTrue(main._verify_otp(conn, "password_reset", code, user_id=self.user_id))
        # But the same code can't be used twice.
        with engine.begin() as conn:
            self.assertFalse(main._verify_otp(conn, "password_reset", code, user_id=self.user_id))

    def test_attempt_cap_blocks_even_the_correct_code(self):
        with engine.begin() as conn:
            code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
        with engine.begin() as conn:
            for _ in range(auth.OTP_MAX_ATTEMPTS):
                self.assertFalse(main._verify_otp(conn, "password_reset", "000000", user_id=self.user_id))
        # Cap now hit — even the correct code is refused, not just further wrong ones.
        with engine.begin() as conn:
            self.assertFalse(main._verify_otp(conn, "password_reset", code, user_id=self.user_id))

    def test_expired_code_fails(self):
        with engine.begin() as conn:
            code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
            conn.execute(
                text("UPDATE otp_codes SET expires_at = :past WHERE user_id = :uid AND used_at IS NULL"),
                {"past": datetime.now(timezone.utc) - timedelta(seconds=1), "uid": self.user_id},
            )
        with engine.begin() as conn:
            self.assertFalse(main._verify_otp(conn, "password_reset", code, user_id=self.user_id))

    def test_reissue_invalidates_the_prior_code(self):
        with engine.begin() as conn:
            old_code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
        with engine.begin() as conn:
            new_code = main._issue_otp(conn, "password_reset", "otp-test@example.com", user_id=self.user_id)
        self.assertNotEqual(old_code, new_code)
        with engine.begin() as conn:
            self.assertFalse(main._verify_otp(conn, "password_reset", old_code, user_id=self.user_id))
        with engine.begin() as conn:
            self.assertTrue(main._verify_otp(conn, "password_reset", new_code, user_id=self.user_id))

    def test_registration_purpose_uses_registration_request_id_binding(self):
        with engine.begin() as conn:
            registration_id = conn.execute(
                text(
                    """
                    INSERT INTO facility_registration_requests
                        (facility_name, facility_type, address, doh_license_number, contact_person, email, phone, password_hash)
                    VALUES ('Test Facility', 'hospital', '123 Test St', 'DOH-TEST', 'Test Person', :email, '09170000000', 'x')
                    RETURNING id
                    """
                ),
                {"email": f"registrant-{self.tag}@example.com"},
            ).scalar()
        try:
            with engine.begin() as conn:
                code = main._issue_otp(
                    conn, "registration_email_verify", "applicant@example.com",
                    registration_request_id=registration_id,
                )
            with engine.begin() as conn:
                self.assertTrue(
                    main._verify_otp(
                        conn, "registration_email_verify", code, registration_request_id=registration_id
                    )
                )
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM otp_codes WHERE registration_request_id = :rid"),
                    {"rid": registration_id},
                )
                conn.execute(
                    text("DELETE FROM facility_registration_requests WHERE id = :rid"),
                    {"rid": registration_id},
                )


if __name__ == "__main__":
    unittest.main()
