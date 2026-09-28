"""Deactivating a facility (PATCH /admin/facilities/{id}, is_active=false)
must make it disappear everywhere it could otherwise be discovered or
targeted, not just block new logins. Covers the three paths that were
checking profile_completed / facility_type but not is_active:
  - GET /facilities            (list_facilities)
  - GET /facilities/nearby     (get_nearby_facilities — Emergency Sourcing)
  - POST /requests             (create_request — request target)

DB-backed, route handlers called directly as plain Python functions, same
convention as test_admin_create_account_for_facility.py.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_facility_deactivation -v
"""

import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy import text

import main
from database import engine


def _make_facility(
    conn, name: str, facility_type: str = "bloodbank",
    is_active: bool = True, profile_completed: bool = False,
    latitude: float = None, longitude: float = None,
) -> int:
    return conn.execute(
        text(
            """
            INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude)
            VALUES (:n, :t, :a, :p, :lat, :lon)
            RETURNING id
            """
        ),
        {"n": name, "t": facility_type, "a": is_active, "p": profile_completed, "lat": latitude, "lon": longitude},
    ).scalar()


def _cleanup_facility(conn, facility_id: int) -> None:
    conn.execute(text("DELETE FROM requests WHERE requesting_facility_id = :fid OR supplying_facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM notifications WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM users WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM facilities WHERE id = :fid"), {"fid": facility_id})


class CreateRequestBody:
    def __init__(self, supplying_facility_id: int, blood_type: str = "O+", quantity: int = 1, emergency_type: str = "restock"):
        self.supplying_facility_id = supplying_facility_id
        self.blood_type = blood_type
        self.quantity = quantity
        self.emergency_type = emergency_type


class FacilityDeactivationTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self._facility_ids = []

    def tearDown(self):
        with engine.begin() as conn:
            for fid in self._facility_ids:
                _cleanup_facility(conn, fid)

    def _facility(self, **kwargs) -> int:
        with engine.begin() as conn:
            fid = _make_facility(conn, f"Test Facility {self.tag} {len(self._facility_ids)}", **kwargs)
        self._facility_ids.append(fid)
        return fid

    def test_list_facilities_excludes_inactive(self):
        active_id = self._facility(is_active=True)
        inactive_id = self._facility(is_active=False)

        ids = {row["id"] for row in main.list_facilities()}
        self.assertIn(active_id, ids)
        self.assertNotIn(inactive_id, ids)

    def test_nearby_search_excludes_inactive_bloodbank(self):
        origin_id = self._facility(facility_type="hospital", profile_completed=True, latitude=14.5, longitude=121.0)
        active_bank_id = self._facility(
            facility_type="bloodbank", is_active=True, profile_completed=True, latitude=14.6, longitude=121.1
        )
        inactive_bank_id = self._facility(
            facility_type="bloodbank", is_active=False, profile_completed=True, latitude=14.6, longitude=121.1
        )

        results = main.get_nearby_facilities(blood_type="O+", quantity=1, acting_facility_id=origin_id)
        ids = {r["id"] for r in results}
        self.assertIn(active_bank_id, ids)
        self.assertNotIn(inactive_bank_id, ids)

    def test_create_request_rejects_inactive_supplying_facility(self):
        requester_id = self._facility(facility_type="hospital")
        inactive_bank_id = self._facility(facility_type="bloodbank", is_active=False)

        with self.assertRaises(HTTPException) as ctx:
            main.create_request(CreateRequestBody(supplying_facility_id=inactive_bank_id), requesting_facility_id=requester_id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not currently accepting requests", ctx.exception.detail)

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM requests WHERE supplying_facility_id = :fid"), {"fid": inactive_bank_id}
            ).scalar()
        self.assertEqual(count, 0)

    def test_create_request_still_accepts_active_supplying_facility(self):
        requester_id = self._facility(facility_type="hospital")
        active_bank_id = self._facility(facility_type="bloodbank", is_active=True)

        result = main.create_request(CreateRequestBody(supplying_facility_id=active_bank_id), requesting_facility_id=requester_id)
        self.assertEqual(result["supplying_facility_id"], active_bank_id)


if __name__ == "__main__":
    unittest.main()
