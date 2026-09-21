"""Tests for the expired-units correctness fix: expired blood_units must
never count as usable/available stock (Dashboard/inventory counts,
threshold/shortage detection, forecast snapshots, emergency-sourcing
availability), while still being visible (not hard-deleted) until a facility
explicitly archives them.

Unlike this project's other tests (test_historical_upload.py,
test_inventory_upload.py, test_prediction_interval.py, and last turn's
test_sarimax_facility_forecast.py), these ARE DB-backed — deliberately, not
by accident. The behavior under test (expires_date >= CURRENT_DATE filtering,
archived_at bulk-updates) lives in raw SQL predicates inside the route
handlers, not in an extractable pure function the way _linear_trend or
dengue_season_index are; a Python-side re-implementation of "is this unit
usable" would test a shadow copy of the logic, not the actual SQL that runs
in production, which is exactly the kind of divergence this fix exists to
prevent. So these tests run against the real dev DATABASE_URL (same one
every other manual verification in this project already uses), each creating
its own disposable facility/facilities in setUp and deleting everything it
touched in tearDown, so they're safe to re-run and never pollute the real
6 curated facilities.

Route handlers are called directly as plain Python functions (e.g.
main.get_inventory_summary(facility_id=...)), the same way FastAPI's own
Depends() defaults are bypassed in this project's manual smoke-testing —
Depends() only resolves via real HTTP request handling, so a direct call just
uses whatever we pass as facility_id.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_expired_inventory -v
"""

import unittest
import uuid
from datetime import date, timedelta

from sqlalchemy import text

import main
from database import engine

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _make_facility(conn, name: str, facility_type: str = "bloodbank") -> int:
    return conn.execute(
        text(
            "INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude) "
            "VALUES (:n, :t, true, true, 14.5995, 120.9842) RETURNING id"
        ),
        {"n": name, "t": facility_type},
    ).scalar()


def _add_unit(conn, facility_id: int, expires_date: date, blood_type: str = "O+", din: str | None = None) -> str:
    din = din or f"TEST-{facility_id}-{uuid.uuid4().hex[:10]}"
    conn.execute(
        text(
            """
            INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id)
            VALUES (:din, :bt, 'Packed RBC', 'Fridge A', 280, :collected, :expires, :fid)
            """
        ),
        {"din": din, "bt": blood_type, "collected": expires_date - timedelta(days=30), "expires": expires_date, "fid": facility_id},
    )
    return din


def _cleanup_facility(conn, facility_id: int) -> None:
    conn.execute(text("DELETE FROM blood_units WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM inventory_snapshots WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM forecast_alert_state WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM notifications WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM facilities WHERE id = :fid"), {"fid": facility_id})


class ExpiryBoundaryUsableCountTests(unittest.TestCase):
    """A unit expiring today is still usable; one that expired yesterday is
    not. Tomorrow is included too as an unambiguous control."""

    def setUp(self):
        with engine.begin() as conn:
            self.facility_id = _make_facility(conn, "__test_expiry_boundary__")
            _add_unit(conn, self.facility_id, YESTERDAY, "O+")
            _add_unit(conn, self.facility_id, TODAY, "O+")
            _add_unit(conn, self.facility_id, TOMORROW, "O+")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_id)

    def test_only_today_and_tomorrow_count_as_usable(self):
        summary = main.get_inventory_summary(facility_id=self.facility_id)
        o_pos = next(row for row in summary if row["blood_type"] == "O+")
        # 2 usable (today, tomorrow) out of 3 inserted (yesterday excluded).
        self.assertEqual(o_pos["units"], 2)

    def test_get_inventory_still_lists_the_expired_unit_for_visibility(self):
        # Not hard-deleted, not hidden — still visible in the raw list so
        # staff can see it and choose to archive it.
        rows = main.get_inventory(facility_id=self.facility_id)
        self.assertEqual(len(rows), 3)
        expired_rows = [r for r in rows if r["expires_date"] < TODAY]
        self.assertEqual(len(expired_rows), 1)
        self.assertEqual(expired_rows[0]["expires_date"], YESTERDAY)


class ExpiredExcludedFromAvailabilityTests(unittest.TestCase):
    """A facility whose only stock of a type is expired must never be
    offered as an available (or compatible-alternative) source for it."""

    def setUp(self):
        with engine.begin() as conn:
            self.origin_id = _make_facility(conn, "__test_avail_origin__")
            self.supplier_id = _make_facility(conn, "__test_avail_supplier__")
            # Only expired O+ stock at the candidate supplier.
            for _ in range(5):
                _add_unit(conn, self.supplier_id, YESTERDAY, "O+")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.origin_id)
            _cleanup_facility(conn, self.supplier_id)

    def test_supplier_with_only_expired_stock_is_not_offered_as_available(self):
        results = main.get_nearby_facilities(blood_type="O+", quantity=1, acting_facility_id=self.origin_id)
        supplier_result = next(r for r in results if r["id"] == self.supplier_id)
        # matching_units counts only non-expired stock server-side — 5
        # expired units at this facility must read as 0, not 5.
        self.assertEqual(supplier_result["matching_units"], 0)
        self.assertFalse(supplier_result["available"])


class ShortageDetectionUsesUsableOnlyStockTests(unittest.TestCase):
    """A hospital padded with only expired stock must still show as below
    minimum — expired units can't mask a real shortage."""

    def setUp(self):
        with engine.begin() as conn:
            self.minimum = conn.execute(
                text("SELECT minimum_units FROM blood_type_thresholds WHERE blood_type = 'O+'")
            ).scalar()
            self.facility_id = _make_facility(conn, "__test_shortage__", facility_type="hospital")
            # Raw count comfortably clears minimum, but every single unit is
            # expired — usable count is 0, so this must still read as a
            # shortage if the fix is correct.
            for _ in range(self.minimum + 5):
                _add_unit(conn, self.facility_id, YESTERDAY, "O+")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_id)

    def test_below_minimum_despite_raw_count_exceeding_it(self):
        with engine.connect() as conn:
            view = main._build_hospital_threshold_view(conn, self.facility_id, is_dengue_season=False)
        o_pos = next(t for t in view["thresholds"] if t["blood_type"] == "O+")
        self.assertEqual(o_pos["units"], 0)
        self.assertEqual(o_pos["status"], "below_minimum")
        self.assertEqual(o_pos["deficit"], self.minimum)


class ArchiveExpiredBacklogTests(unittest.TestCase):
    """The bulk-archive endpoint clears the backlog (declutters GET
    /inventory) without ever deleting rows or changing any usable count."""

    def setUp(self):
        with engine.begin() as conn:
            self.facility_id = _make_facility(conn, "__test_archive__")
            self.expired_dins = [_add_unit(conn, self.facility_id, YESTERDAY, "A+") for _ in range(3)]
            self.usable_din = _add_unit(conn, self.facility_id, TOMORROW, "A+")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_id)

    def test_archive_expired_archives_only_expired_units_and_reports_count(self):
        result = main.archive_expired_inventory(facility_id=self.facility_id)
        self.assertEqual(result["archived_count"], 3)

        with engine.connect() as conn:
            archived = conn.execute(
                text("SELECT din, archived_at FROM blood_units WHERE facility_id = :fid ORDER BY din"),
                {"fid": self.facility_id},
            ).mappings().all()
        by_din = {row["din"]: row["archived_at"] for row in archived}
        for din in self.expired_dins:
            self.assertIsNotNone(by_din[din])
        self.assertIsNone(by_din[self.usable_din])

    def test_get_inventory_excludes_archived_rows_afterward(self):
        main.archive_expired_inventory(facility_id=self.facility_id)
        rows = main.get_inventory(facility_id=self.facility_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["din"], self.usable_din)

    def test_archiving_does_not_change_the_usable_count_it_was_already_zero_for_expired(self):
        before = next(r["units"] for r in main.get_inventory_summary(facility_id=self.facility_id) if r["blood_type"] == "A+")
        main.archive_expired_inventory(facility_id=self.facility_id)
        after = next(r["units"] for r in main.get_inventory_summary(facility_id=self.facility_id) if r["blood_type"] == "A+")
        self.assertEqual(before, 1)  # only the tomorrow-expiring unit was ever usable
        self.assertEqual(before, after)

    def test_archiving_twice_in_a_row_is_a_no_op_the_second_time(self):
        main.archive_expired_inventory(facility_id=self.facility_id)
        second = main.archive_expired_inventory(facility_id=self.facility_id)
        self.assertEqual(second["archived_count"], 0)


if __name__ == "__main__":
    unittest.main()
