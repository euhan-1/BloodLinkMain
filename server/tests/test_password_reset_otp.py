"""POST /auth/forgot-password/otp + POST /auth/reset-password/otp — the
typed-code twin of the existing link-based flow (POST /auth/forgot-password
+ POST /auth/reset-password), built to run alongside it, not replace it.

email_service.send_email is mocked (unittest.mock, stdlib) — same reasoning
as the other registration/reset tests: what's under test is this endpoint's
own logic, not whether Resend can deliver to example.com.

DB-backed, route handlers called directly, same convention as this suite's
other endpoint tests.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_password_reset_otp -v
"""

import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text

import auth
import main
from database import engine


class ForgotPasswordOtpBody:
    def __init__(self, email: str):
        self.email = email


class ResetPasswordOtpBody:
    def __init__(self, email: str, code: str, new_password: str):
        self.email = email
        self.code = code
        self.new_password = new_password


class FakeRequest:
    class _Client:
        host = "203.0.113.7"

    def __init__(self):
        self.headers = {}
        self.client = self._Client()


class PasswordResetOtpTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"reset-otp-{self.tag}@example.com"
        with engine.begin() as conn:
            self.facility_id = conn.execute(
                text("INSERT INTO facilities (name, facility_type, is_active) VALUES ('Test', 'hospital', true) RETURNING id")
            ).scalar()
            self.user_id = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, facility_id, role) "
                    "VALUES (:email, :password_hash, :fid, 'staff') RETURNING id"
                ),
                {"email": self.email, "password_hash": auth.hash_password("OldPassword123"), "fid": self.facility_id},
            ).scalar()
        main._FORGOT_LIMITER.clear(f"203.0.113.7|{self.email}")

    def tearDown(self):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM otp_codes WHERE user_id = :uid"), {"uid": self.user_id})
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.user_id})
            conn.execute(text("DELETE FROM facilities WHERE id = :fid"), {"fid": self.facility_id})
        main._FORGOT_LIMITER.clear(f"203.0.113.7|{self.email}")

    @patch("main.email_service.send_email")
    def test_issues_code_for_eligible_account(self, mock_send):
        result = main.forgot_password_otp(ForgotPasswordOtpBody(email=self.email), FakeRequest())
        self.assertEqual(result, main._FORGOT_PASSWORD_OTP_RESPONSE)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to"], self.email)

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM otp_codes WHERE user_id = :uid AND purpose = 'password_reset'"),
                {"uid": self.user_id},
            ).scalar()
        self.assertEqual(count, 1)

    @patch("main.email_service.send_email")
    def test_identical_response_for_unknown_email(self, mock_send):
        result = main.forgot_password_otp(ForgotPasswordOtpBody(email="nobody-here@example.com"), FakeRequest())
        self.assertEqual(result, main._FORGOT_PASSWORD_OTP_RESPONSE)
        mock_send.assert_not_called()

    @patch("main.email_service.send_email")
    def test_deactivated_facility_gets_generic_response_and_no_code(self, mock_send):
        with engine.begin() as conn:
            conn.execute(text("UPDATE facilities SET is_active = false WHERE id = :fid"), {"fid": self.facility_id})
        result = main.forgot_password_otp(ForgotPasswordOtpBody(email=self.email), FakeRequest())
        self.assertEqual(result, main._FORGOT_PASSWORD_OTP_RESPONSE)
        mock_send.assert_not_called()

    def _issue_code(self) -> str:
        with engine.begin() as conn:
            return main._issue_otp(conn, "password_reset", self.email, user_id=self.user_id)

    def test_correct_code_resets_password(self):
        code = self._issue_code()
        result = main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code=code, new_password="BrandNewPass123"))
        self.assertEqual(result["message"], "Password updated. You can now log in with your new password.")

        with engine.connect() as conn:
            password_hash = conn.execute(text("SELECT password_hash FROM users WHERE id = :uid"), {"uid": self.user_id}).scalar()
        self.assertTrue(auth.verify_password("BrandNewPass123", password_hash))

    def test_wrong_code_rejected_and_password_unchanged(self):
        self._issue_code()
        with self.assertRaises(HTTPException) as ctx:
            main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code="000000", new_password="BrandNewPass123"))
        self.assertEqual(ctx.exception.status_code, 400)

        with engine.connect() as conn:
            password_hash = conn.execute(text("SELECT password_hash FROM users WHERE id = :uid"), {"uid": self.user_id}).scalar()
        self.assertTrue(auth.verify_password("OldPassword123", password_hash))

    def test_code_is_single_use(self):
        code = self._issue_code()
        main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code=code, new_password="FirstNewPass123"))
        with self.assertRaises(HTTPException):
            main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code=code, new_password="SecondNewPass123"))

    def test_attempt_cap_blocks_even_the_correct_code(self):
        code = self._issue_code()
        for _ in range(auth.OTP_MAX_ATTEMPTS):
            with self.assertRaises(HTTPException):
                main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code="000000", new_password="BrandNewPass123"))
        with self.assertRaises(HTTPException):
            main.reset_password_otp(ResetPasswordOtpBody(email=self.email, code=code, new_password="BrandNewPass123"))

    def test_unknown_email_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            main.reset_password_otp(
                ResetPasswordOtpBody(email="nobody-here@example.com", code="123456", new_password="BrandNewPass123")
            )
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
