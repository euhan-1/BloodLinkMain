"""What the blood bank Dashboard reads: can_give per type, expiring units grouped, one notification per group.

can_give comes from _releasable_counts, the predicate release selects from and the nearby search counts, so it is what another
facility's search would see available from this bank (units above the type's minimum), including the per-component expiry
margin and units already reserved for a request. Expiring units come grouped by type, component and day, not one row per DIN.
Notifying nearby hospitals is one notification per hospital per group.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import accepted_request, add


def set_thresholds(facility_id, minimum, maximum=100, types=("O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-")):
    with engine.begin() as c:
        for t in types:
            c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, :t, :mn, :mx)"),
                      {"f": facility_id, "t": t, "mn": minimum, "mx": maximum})


class BankDashboardBase(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def summary(self, facility=None):
        return {r["blood_type"]: r for r in app.get_inventory_summary(facility_id=facility or self.bank)}


class CanGiveTests(BankDashboardBase):
    def test_it_is_the_releasable_units_above_the_minimum_per_component(self):
        set_thresholds(self.bank, 4)
        for n in range(10):
            add(self.bank, self.din(n), 20)                              # releasable
        add(self.bank, self.din("SOON1"), 1)                              # inside the packed RBC margin
        add(self.bank, self.din("SOON2"), 2)
        add(self.bank, self.din("PLT"), 20, component="Platelets")
        row = self.summary()["O+"]
        self.assertEqual(row["units"], 13)                                # everything usable on hand, as before
        self.assertEqual(row["can_give"], {"Packed RBC": 6, "Platelets": 0})   # 10 releasable - 4 minimum; 1 platelet is under it

    def test_a_unit_reserved_for_a_request_is_not_given_twice(self):
        set_thresholds(self.bank, 2)
        for n in range(6):
            add(self.bank, self.din(n), 20)
        self.assertEqual(self.summary()["O+"]["can_give"]["Packed RBC"], 4)
        rid = accepted_request(self.bank, self.hospital, 3)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        row = self.summary()["O+"]
        self.assertEqual((row["units"], row["can_give"]["Packed RBC"]), (6, 1))   # still on hand, but 3 are spoken for: 3 - 2

    def test_below_the_minimum_there_is_nothing_to_give_never_a_negative(self):
        set_thresholds(self.bank, 50)
        add(self.bank, self.din(1), 20)
        self.assertEqual(self.summary()["O+"]["can_give"], {"Packed RBC": 0, "Platelets": 0})

    def test_a_type_with_no_units_gives_zero(self):
        set_thresholds(self.bank, 3)
        self.assertEqual(self.summary()["A-"]["can_give"], {"Packed RBC": 0, "Platelets": 0})

    def test_it_is_what_another_facilitys_search_sees_available(self):
        # The point of one predicate: the Dashboard's number is the search's number (above the reserve), for every component.
        set_thresholds(self.bank, 3)
        for n in range(8):
            add(self.bank, self.din(n), 20)
        add(self.bank, self.din("SOON"), 1)
        add(self.bank, self.din("PLT1"), 20, component="Platelets")
        add(self.bank, self.din("PLT2"), 20, component="Platelets")
        add(self.bank, self.din("PLT3"), 20, component="Platelets")
        add(self.bank, self.din("PLT4"), 20, component="Platelets")
        rid = accepted_request(self.bank, self.hospital, 2)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        for blood_type in ("O+", "A+"):
            for component in app.COMPONENTS:
                with self.subTest(blood_type, component=component):
                    found = next(r for r in app.get_nearby_facilities(blood_type=blood_type, component=component, quantity=1,
                                                                      acting_facility_id=self.hospital) if r["id"] == self.bank)
                    self.assertEqual(self.summary()[blood_type]["can_give"][component], max(0, found["usable_units"]))

    def test_a_hospital_has_no_can_give(self):
        set_thresholds(self.hospital, 2)
        add(self.hospital, self.din(1), 20)
        row = self.summary(self.hospital)["O+"]
        self.assertEqual((row["units"], row["can_give"]), (1, {}))

    def test_the_existing_fields_are_unchanged(self):
        set_thresholds(self.bank, 4, 90)
        add(self.bank, self.din(1), 20)
        row = self.summary()["O+"]
        self.assertEqual({k: row[k] for k in ("blood_type", "minimum_units", "maximum_units", "units")},
                         {"blood_type": "O+", "minimum_units": 4, "maximum_units": 90, "units": 1})


class ExpiringGroupsTests(BankDashboardBase):
    def groups(self, facility=None):
        return app.get_expiring_inventory(facility_id=facility or self.bank)

    def test_units_are_grouped_by_type_component_and_day_soonest_first(self):
        for n in range(3):
            add(self.bank, self.din(f"A{n}"), 2)
        add(self.bank, self.din("B"), 2, blood_type="A+")
        add(self.bank, self.din("P"), 2, component="Platelets")
        add(self.bank, self.din("LATER"), 5)
        got = self.groups()
        self.assertEqual([(g["blood_type"], g["component"], g["days_left"], g["units"]) for g in got["groups"]],
                         [("A+", "Packed RBC", 2, 1), ("O+", "Packed RBC", 2, 3), ("O+", "Platelets", 2, 1), ("O+", "Packed RBC", 5, 1)])
        self.assertEqual((got["total_units"], got["window_days"]), (6, 7))

    def test_the_window_is_today_through_seven_days_and_nothing_else(self):
        for days in (-1, 0, 7, 8):
            add(self.bank, self.din(f"D{days}"), days)
        self.assertEqual([g["days_left"] for g in self.groups()["groups"]], [0, 7])

    def test_reserved_units_are_leaving_so_they_are_not_listed(self):
        add(self.bank, self.din("R"), 5)
        add(self.bank, self.din("FREE"), 5)
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, component, quantity, "
                                 "emergency_type, status) VALUES (:h, :b, 'O+', 'Packed RBC', 1, 'restock', 'accepted') RETURNING id"),
                            {"h": self.hospital, "b": self.bank}).scalar()
            c.execute(text("UPDATE blood_units SET reserved_for_request_id = :r WHERE din = :d"), {"r": rid, "d": self.din("R")})
        self.assertEqual(self.groups()["total_units"], 1)

    def test_only_the_facilitys_own_units(self):
        add(self.bank, self.din("MINE"), 3)
        other_bank, _ = make_pair(self, self.tag + "B")
        add(other_bank, f"{self.tag}-THEIRS", 3)
        self.assertEqual(self.groups()["total_units"], 1)
        self.assertEqual(self.groups(other_bank)["total_units"], 1)

    def test_nothing_expiring_is_an_empty_list_not_an_error(self):
        add(self.bank, self.din(1), 30)
        self.assertEqual(self.groups(), {"window_days": 7, "total_units": 0, "groups": []})

    def test_the_once_per_tier_expiry_notification_still_fires_from_here(self):
        add(self.bank, self.din("CRIT"), 2)
        add(self.bank, self.din("NEAR"), 6)
        add(self.bank, self.din("OK"), 20)
        self.groups()
        self.groups()  # idempotent: a second load does not notify again
        sent = [m for (m,) in q("SELECT message FROM notifications WHERE facility_id = :f AND type = 'unit_expiry' ORDER BY id", f=self.bank)]
        self.assertEqual(len(sent), 2)
        self.assertTrue(any("critical" in m for m in sent) and any("near expiry" in m for m in sent))
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id = :f AND last_notified_expiry_status IS NOT NULL", f=self.bank)[0][0], 2)


class NotifyGroupTests(BankDashboardBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.drop_stray_notifications)

    def drop_stray_notifications(self):
        # Hospitals left behind by other tests in the same session would also be told; do not leave their rows around.
        bank_name = q("SELECT name FROM facilities WHERE id = :i", i=self.bank)
        if bank_name:
            with engine.begin() as c:
                c.execute(text("DELETE FROM notifications WHERE type = 'expiring_unit_alert' AND message LIKE :p"), {"p": f"{bank_name[0][0]}%"})

    def body(self, **over):
        return app.NotifyExpiringGroupBody(**{"blood_type": "O+", "component": "Packed RBC", "expires_date": TODAY + timedelta(days=3), **over})

    def mine(self):
        return q("SELECT message, link, facility_id FROM notifications WHERE type = 'expiring_unit_alert' AND facility_id = :h", h=self.hospital)

    def test_one_notification_per_hospital_for_the_whole_group(self):
        for n in range(4):
            add(self.bank, self.din(n), 3)
        result = app.notify_nearby_hospitals_of_expiring_group(self.body(), acting_facility_id=self.bank)
        self.assertEqual(result["units"], 4)
        self.assertGreaterEqual(result["notified_count"], 1)
        rows = self.mine()
        self.assertEqual(len(rows), 1)                                   # one for the group, not four for the units
        message, link, _ = rows[0]
        self.assertIn("4 near-expiry O+ Packed RBC units that expire in 3 days", message)
        self.assertEqual(link, "sourcing:O+")

    def test_wording_for_one_unit_and_for_today(self):
        add(self.bank, self.din(1), 0)
        app.notify_nearby_hospitals_of_expiring_group(self.body(expires_date=TODAY), acting_facility_id=self.bank)
        self.assertIn("1 near-expiry O+ Packed RBC unit that expires today", self.mine()[0][0])

    def test_only_active_located_hospitals_are_told_and_never_banks(self):
        add(self.bank, self.din(1), 3)
        with engine.begin() as c:
            ids = [c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude) "
                                  "VALUES (:n, :t, :a, true, :lat, 121.0) RETURNING id"),
                             {"n": f"NG {self.tag} {i}", "t": t, "a": a, "lat": lat}).scalar()
                   for i, (t, a, lat) in enumerate([("hospital", False, 14.6), ("hospital", True, None), ("bloodbank", True, 14.6)])]
        self.addCleanup(lambda: [self._drop_facility(i) for i in ids])
        app.notify_nearby_hospitals_of_expiring_group(self.body(), acting_facility_id=self.bank)
        for fid in ids:
            self.assertEqual(q("SELECT count(*) FROM notifications WHERE facility_id = :f", f=fid)[0][0], 0)
        self.assertEqual(len(self.mine()), 1)

    def _drop_facility(self, fid):
        with engine.begin() as c:
            c.execute(text("DELETE FROM notifications WHERE facility_id = :f"), {"f": fid})
            c.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id = :f"), {"f": fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": fid})

    def test_it_refuses_what_is_not_a_near_expiry_group_of_this_facilitys_own_units(self):
        add(self.bank, self.din("FAR"), 20)
        other_bank, _ = make_pair(self, self.tag + "B")
        add(other_bank, f"{self.tag}-OTHER", 3)
        cases = [
            (self.body(expires_date=TODAY - timedelta(days=1)), 400),             # already expired
            (self.body(expires_date=TODAY + timedelta(days=8)), 400),             # not near-expiry yet
            (self.body(expires_date=TODAY + timedelta(days=3)), 404),             # nothing of this group here (the other bank's is not ours)
            (self.body(component="Platelets", expires_date=TODAY + timedelta(days=20)), 400),
        ]
        for body, status in cases:
            with self.subTest(body.expires_date.isoformat(), component=body.component):
                with self.assertRaises(app.HTTPException) as ctx:
                    app.notify_nearby_hospitals_of_expiring_group(body, acting_facility_id=self.bank)
                self.assertEqual(ctx.exception.status_code, status)
        self.assertEqual(self.mine(), [])

    def test_reserved_units_do_not_count_as_a_group(self):
        add(self.bank, self.din("R"), 3)
        rid = accepted_request(self.bank, self.hospital, 1)
        with engine.begin() as c:
            c.execute(text("UPDATE blood_units SET reserved_for_request_id = :r WHERE din = :d"), {"r": rid, "d": self.din("R")})
        with self.assertRaises(app.HTTPException) as ctx:
            app.notify_nearby_hospitals_of_expiring_group(self.body(), acting_facility_id=self.bank)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_facility_without_a_location_cannot_notify(self):
        add(self.bank, self.din(1), 3)
        with engine.begin() as c:
            c.execute(text("UPDATE facilities SET latitude = NULL WHERE id = :f"), {"f": self.bank})
        with self.assertRaises(app.HTTPException) as ctx:
            app.notify_nearby_hospitals_of_expiring_group(self.body(), acting_facility_id=self.bank)
        self.assertEqual(ctx.exception.status_code, 400)


class ProjectedLowTests(unittest.TestCase):
    def test_a_recommendation_says_how_low_the_projection_goes(self):
        points = {0: 100.0, 5: 90.0, 10: 70.0, 15: 55.0, 20: 60.0, 25: 62.0, 30: 64.0}
        rec = app._compute_restock_recommendation("O+", points, minimum=80, maximum=160, today=TODAY)
        self.assertEqual((rec["recommended_units"], rec["projected_low_units"]), (105, 55))   # 160 - 55

    def test_the_figure_is_an_estimate_beside_current_stock_so_it_is_a_whole_number(self):
        points = {c: 79.6 for c in app.FORECAST_CHECKPOINTS}
        self.assertEqual(app._compute_restock_recommendation("O+", points, 80, 160, TODAY)["projected_low_units"], 80)


if __name__ == "__main__":
    unittest.main()
