"""GET /admin/registrations, POST /admin/registrations/{id}/approve,
POST /admin/registrations/{id}/reject.

email_service.send_email is mocked (unittest.mock, stdlib) — same reasoning
as test_facility_registration.py: Resend's sandbox sender can't deliver to
example.com, so a real call here would test Resend, not this endpoint.

DB-backed otherwise, route handlers called directly as plain Python
functions, same convention as this suite's other admin-endpoint tests.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_admin_registrations -v
"""

import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

import auth
import main
from database import engine


class RejectRegistrationBody:
    def __init__(self, reason: str):
        self.reason = reason


class AdminRegistrationsTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"regadmin-{self.tag}@example.com"
        with engine.connect() as conn:
            admin_id = conn.execute(text("SELECT id FROM users WHERE role = 'admin' LIMIT 1")).scalar()
        # matches require_admin_role's decoded-token shape; reviewed_by is a
        # real FK to users(id), so this needs an actual admin row to point at.
        self.admin = {"sub": str(admin_id)}

    def tearDown(self):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM otp_codes WHERE registration_request_id IN "
                    "(SELECT id FROM facility_registration_requests WHERE email = :email)"
                ),
                {"email": self.email},
            )
            conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": self.email})
            conn.execute(
                text(
                    "DELETE FROM blood_type_thresholds WHERE facility_id IN "
                    "(SELECT facility_id FROM users WHERE email = :email) "
                    "OR facility_id IN (SELECT id FROM facilities WHERE name = :name)"
                ),
                {"email": self.email, "name": f"Test Facility {self.tag}"},
            )
            conn.execute(text("DELETE FROM facilities WHERE name = :name"), {"name": f"Test Facility {self.tag}"})
            conn.execute(text("DELETE FROM facility_registration_requests WHERE email = :email"), {"email": self.email})

    def _make_registration(self, status: str = "email_verified") -> int:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    """
                    INSERT INTO facility_registration_requests
                        (facility_name, facility_type, address, latitude, longitude, doh_license_number,
                         contact_person, email, phone, password_hash, status)
                    VALUES
                        (:name, 'hospital', '123 Test St', 14.5, 121.0, 'DOH-TEST', 'Test Person',
                         :email, '09170000000', :password_hash, :status)
                    RETURNING id
                    """
                ),
                {
                    "name": f"Test Facility {self.tag}", "email": self.email,
                    "password_hash": auth.hash_password("Sup3rSecret!"), "status": status,
                },
            ).scalar()

    @patch("main.email_service.send_email")
    def test_queue_includes_only_email_verified_rows(self, mock_send):
        verified_id = self._make_registration(status="email_verified")
        ids = {row["id"] for row in main.admin_list_registrations()}
        self.assertIn(verified_id, ids)

        with engine.begin() as conn:
            conn.execute(text("UPDATE facility_registration_requests SET status = 'submitted' WHERE id = :id"), {"id": verified_id})
        ids = {row["id"] for row in main.admin_list_registrations()}
        self.assertNotIn(verified_id, ids)

    @patch("main.email_service.send_email")
    def test_approve_creates_facility_and_user_and_archives_request(self, mock_send):
        registration_id = self._make_registration()
        result = main.admin_approve_registration(registration_id, admin=self.admin)

        self.assertEqual(result["facility"]["name"], f"Test Facility {self.tag}")
        self.assertTrue(result["facility"]["profile_completed"])
        self.assertEqual(result["user"]["email"], self.email)

        with engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT must_change_password, password_hash FROM users WHERE email = :email"), {"email": self.email}
            ).mappings().first()
            reg_row = conn.execute(
                text("SELECT status, archived_at, reviewed_by FROM facility_registration_requests WHERE id = :id"),
                {"id": registration_id},
            ).mappings().first()
        # Self-registered accounts keep their own chosen password and skip
        # the forced-reset flow entirely.
        self.assertFalse(user_row["must_change_password"])
        self.assertTrue(auth.verify_password("Sup3rSecret!", user_row["password_hash"]))
        self.assertEqual(reg_row["status"], "approved")
        self.assertIsNotNone(reg_row["archived_at"])
        self.assertEqual(reg_row["reviewed_by"], int(self.admin["sub"]))

        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to"], self.email)
        self.assertIn("approved", mock_send.call_args.kwargs["subject"].lower())

    @patch("main.email_service.send_email")
    def test_approve_refuses_a_not_yet_verified_registration(self, mock_send):
        registration_id = self._make_registration(status="submitted")
        with self.assertRaises(HTTPException) as ctx:
            main.admin_approve_registration(registration_id, admin=self.admin)
        self.assertEqual(ctx.exception.status_code, 400)

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM users WHERE email = :email"), {"email": self.email}).scalar()
        self.assertEqual(count, 0)

    @patch("main.email_service.send_email")
    def test_approve_refuses_a_nonexistent_registration(self, mock_send):
        with self.assertRaises(HTTPException) as ctx:
            main.admin_approve_registration(999999999, admin=self.admin)
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("main.email_service.send_email")
    def test_approve_twice_refuses_the_second_time(self, mock_send):
        registration_id = self._make_registration()
        main.admin_approve_registration(registration_id, admin=self.admin)
        with self.assertRaises(HTTPException) as ctx:
            main.admin_approve_registration(registration_id, admin=self.admin)
        # Already archived -> not found, not a second facility/user pair.
        self.assertEqual(ctx.exception.status_code, 404)

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM users WHERE email = :email"), {"email": self.email}).scalar()
        self.assertEqual(count, 1)

    @patch("main.email_service.send_email")
    def test_reject_requires_a_reason(self, mock_send):
        registration_id = self._make_registration()
        with self.assertRaises(ValidationError):
            main.RejectRegistrationBody(reason="   ")
        mock_send.assert_not_called()
        with engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM facility_registration_requests WHERE id = :id"), {"id": registration_id}
            ).scalar()
        self.assertEqual(status, "email_verified")

    @patch("main.email_service.send_email")
    def test_reject_archives_with_reason_and_notifies_applicant(self, mock_send):
        registration_id = self._make_registration()
        result = main.admin_reject_registration(
            registration_id, RejectRegistrationBody(reason="DOH license could not be verified"), admin=self.admin
        )
        self.assertEqual(result, {"message": "registration rejected"})

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, rejection_reason, archived_at FROM facility_registration_requests WHERE id = :id"),
                {"id": registration_id},
            ).mappings().first()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["rejection_reason"], "DOH license could not be verified")
        self.assertIsNotNone(row["archived_at"])

        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to"], self.email)
        self.assertIn("not approved", mock_send.call_args.kwargs["subject"].lower())

        # Rejection must not create a live account.
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM users WHERE email = :email"), {"email": self.email}).scalar()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
