"""Tests for the per-facility inventory thresholds correctness fix:
blood_type_thresholds used to be one shared global table (one row per blood
type, no facility_id) — a facility editing its own threshold silently
changed what every other facility saw too. Now keyed by
(facility_id, blood_type); see migrate_scope_thresholds_to_facility.py.

DB-backed, like test_expired_inventory.py and for the same reason: the
scoping under test lives in raw SQL WHERE clauses inside the route handlers,
not in an extractable pure function — testing a Python-side re-implementation
would prove nothing about the actual queries that run in production. Each
test creates its own disposable facilities in setUp and deletes everything
in tearDown.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_facility_thresholds -v
"""

import unittest

from sqlalchemy import text

import main
from database import engine

# Mirrors main.py's DEFAULT_THRESHOLDS — a test facility isn't created via
# POST /admin/facilities (that needs a real password hash / JWT setup this
# test doesn't need), so it's seeded directly here the same way that
# endpoint would seed it.
TEST_THRESHOLDS = [
    ("A+", 80, 160), ("A-", 50, 100), ("B+", 60, 120), ("B-", 40, 80),
    ("O+", 100, 200), ("O-", 80, 160), ("AB+", 30, 60), ("AB-", 25, 50),
]


def _make_facility(conn, name: str, facility_type: str = "bloodbank") -> int:
    facility_id = conn.execute(
        text(
            "INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude) "
            "VALUES (:n, :t, true, true, 14.5995, 120.9842) RETURNING id"
        ),
        {"n": name, "t": facility_type},
    ).scalar()
    conn.execute(
        text(
            "INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) "
            "VALUES (:facility_id, :bt, :mn, :mx)"
        ),
        [{"facility_id": facility_id, "bt": bt, "mn": mn, "mx": mx} for bt, mn, mx in TEST_THRESHOLDS],
    )
    return facility_id


def _cleanup_facility(conn, facility_id: int) -> None:
    conn.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM blood_units WHERE facility_id = :fid"), {"fid": facility_id})
    conn.execute(text("DELETE FROM facilities WHERE id = :fid"), {"fid": facility_id})


class UpdateThresholdBody:
    """Stand-in for main.UpdateThresholdBody (a pydantic model) with plain
    attribute access — update_threshold only reads .minimum_units/.maximum_units."""
    def __init__(self, minimum_units: int, maximum_units: int):
        self.minimum_units = minimum_units
        self.maximum_units = maximum_units


class TwoFacilitiesIndependentThresholdsTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as conn:
            self.facility_a = _make_facility(conn, "__test_threshold_a__")
            self.facility_b = _make_facility(conn, "__test_threshold_b__")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_a)
            _cleanup_facility(conn, self.facility_b)

    def test_both_facilities_start_with_the_same_default_values(self):
        summary_a = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_a)}
        summary_b = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_b)}
        self.assertEqual(summary_a["O+"]["minimum_units"], 100)
        self.assertEqual(summary_b["O+"]["minimum_units"], 100)

    def test_editing_facility_a_does_not_change_facility_b(self):
        main.update_threshold("O+", UpdateThresholdBody(minimum_units=999, maximum_units=1500), facility_id=self.facility_a)

        summary_a = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_a)}
        summary_b = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_b)}
        self.assertEqual(summary_a["O+"]["minimum_units"], 999)
        self.assertEqual(summary_a["O+"]["maximum_units"], 1500)
        # The bug this fixes: B must still see the untouched default.
        self.assertEqual(summary_b["O+"]["minimum_units"], 100)
        self.assertEqual(summary_b["O+"]["maximum_units"], 200)

    def test_editing_facility_b_after_a_does_not_revert_or_affect_a(self):
        main.update_threshold("A-", UpdateThresholdBody(minimum_units=10, maximum_units=20), facility_id=self.facility_a)
        main.update_threshold("A-", UpdateThresholdBody(minimum_units=70, maximum_units=140), facility_id=self.facility_b)

        summary_a = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_a)}
        summary_b = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_b)}
        self.assertEqual(summary_a["A-"]["minimum_units"], 10)
        self.assertEqual(summary_b["A-"]["minimum_units"], 70)

    def test_cannot_edit_a_blood_type_row_that_belongs_to_another_facility_only(self):
        # Delete facility_b's O- row entirely so only facility_a has one —
        # editing "O-" while acting as facility_b must 404, not silently
        # succeed against someone else's row (or a stray global one).
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM blood_type_thresholds WHERE facility_id = :fid AND blood_type = 'O-'"),
                {"fid": self.facility_b},
            )
        with self.assertRaises(Exception):
            main.update_threshold("O-", UpdateThresholdBody(minimum_units=5, maximum_units=10), facility_id=self.facility_b)
        # facility_a's own O- row must be completely untouched.
        summary_a = {r["blood_type"]: r for r in main.get_inventory_summary(facility_id=self.facility_a)}
        self.assertEqual(summary_a["O-"]["minimum_units"], 80)


class HospitalThresholdViewScopingTests(unittest.TestCase):
    """_build_hospital_threshold_view shares the same bug pattern as
    /inventory/summary — checked independently since it's a separate query,
    not just a caller of the same function."""

    def setUp(self):
        with engine.begin() as conn:
            self.facility_a = _make_facility(conn, "__test_threshold_hosp_a__", facility_type="hospital")
            self.facility_b = _make_facility(conn, "__test_threshold_hosp_b__", facility_type="hospital")

    def tearDown(self):
        with engine.begin() as conn:
            _cleanup_facility(conn, self.facility_a)
            _cleanup_facility(conn, self.facility_b)

    def test_hospital_view_uses_its_own_facilitys_thresholds(self):
        main.update_threshold("B+", UpdateThresholdBody(minimum_units=5, maximum_units=15), facility_id=self.facility_a)

        with engine.connect() as conn:
            view_a = main._build_hospital_threshold_view(conn, self.facility_a, is_dengue_season=False)
            view_b = main._build_hospital_threshold_view(conn, self.facility_b, is_dengue_season=False)

        a_bplus = next(t for t in view_a["thresholds"] if t["blood_type"] == "B+")
        b_bplus = next(t for t in view_b["thresholds"] if t["blood_type"] == "B+")
        self.assertEqual(a_bplus["minimum_units"], 5)
        self.assertEqual(b_bplus["minimum_units"], 60)  # untouched default


class NearbyFacilitiesUsesEachCandidatesOwnThresholdTests(unittest.TestCase):
    """The subtlest part of this fix: GET /facilities/nearby loops over
    OTHER facilities' stock, so each candidate must be judged against ITS
    OWN threshold (its own safety reserve) — not the searching facility's,
    and not any other candidate's."""

    def setUp(self):
        with engine.begin() as conn:
            self.origin = _make_facility(conn, "__test_nearby_origin__")
            self.strict_candidate = _make_facility(conn, "__test_nearby_strict__")
            self.lenient_candidate = _make_facility(conn, "__test_nearby_lenient__")
            # Both candidates hold the exact same real stock (50 O+ units).
            for facility_id in (self.strict_candidate, self.lenient_candidate):
                for i in range(50):
                    conn.execute(
                        text(
                            "INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
                            "VALUES (:din, 'O+', 'Packed RBC', 'Fridge A', 280, CURRENT_DATE - 10, CURRENT_DATE + 20, :fid)"
                        ),
                        {"din": f"TEST-NEARBY-{facility_id}-{i}", "fid": facility_id},
                    )
            # Strict candidate's own O+ minimum is high (60) — 50 units on
            # hand is BELOW its own reserve, so it should NOT be available.
            conn.execute(
                text("UPDATE blood_type_thresholds SET minimum_units = 60 WHERE facility_id = :fid AND blood_type = 'O+'"),
                {"fid": self.strict_candidate},
            )
            # Lenient candidate's own O+ minimum is low (10) — 50 units on
            # hand is comfortably above its own reserve, so it SHOULD be available.
            conn.execute(
                text("UPDATE blood_type_thresholds SET minimum_units = 10 WHERE facility_id = :fid AND blood_type = 'O+'"),
                {"fid": self.lenient_candidate},
            )

    def tearDown(self):
        with engine.begin() as conn:
            for fid in (self.origin, self.strict_candidate, self.lenient_candidate):
                _cleanup_facility(conn, fid)

    def test_each_candidate_is_judged_against_its_own_threshold_not_a_shared_one(self):
        results = main.get_nearby_facilities(blood_type="O+", quantity=1, acting_facility_id=self.origin)
        by_id = {r["id"]: r for r in results}

        strict = by_id[self.strict_candidate]
        lenient = by_id[self.lenient_candidate]

        # Identical real stock (50), but opposite availability — proves the
        # threshold comparison used each facility's OWN row, not a shared
        # global one (which would have made both identical) and not the
        # other candidate's (which would have flipped the outcome).
        self.assertEqual(strict["matching_units"], 50)
        self.assertEqual(lenient["matching_units"], 50)
        self.assertEqual(strict["usable_units"], 50 - 60)  # -10, below its own reserve
        self.assertEqual(lenient["usable_units"], 50 - 10)  # 40, above its own reserve
        self.assertFalse(strict["available"])
        self.assertTrue(lenient["available"])


if __name__ == "__main__":
    unittest.main()
