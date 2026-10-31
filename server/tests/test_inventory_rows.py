"""What GET /inventory says about each unit, for the blood bank Inventory screen.

The endpoint reads every non-archived unit (the screen filters, sorts and pages them itself, and the once-per-tier expiry
notification rides on that full read), and each row says which request it is reserved for, so a reserved unit can explain why it
is not available when someone expects it to be. Archiving the expired backlog skips units still reserved for a request.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import unittest
import uuid

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import make_pair, q
from tests.test_request_component import accepted_request, add


class InventoryRowTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def rows(self):
        return {r["din"]: r for r in app.get_inventory(facility_id=self.bank)}

    def test_a_reserved_unit_says_which_request_holds_it_and_the_rest_say_none(self):
        add(self.bank, self.din("A"), 20)
        add(self.bank, self.din("B"), 25)
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        rows = self.rows()
        held = [d for d, r in rows.items() if r["reserved_for_request_id"] == rid]
        self.assertEqual(held, [self.din("A")])                    # first-expired-first-out took the earlier one
        self.assertIsNone(rows[self.din("B")]["reserved_for_request_id"])

    def test_the_existing_fields_are_all_still_there(self):
        add(self.bank, self.din("A"), 20)
        self.assertEqual(set(self.rows()[self.din("A")]),
                         {"din", "blood_type", "component", "location", "volume_ml", "collected_date", "expires_date", "reserved_for_request_id"})

    def test_expired_units_are_still_listed_and_archived_ones_are_not(self):
        add(self.bank, self.din("LIVE"), 20)
        add(self.bank, self.din("EXPIRED"), -3)
        add(self.bank, self.din("GONE"), -9)
        with engine.begin() as c:
            c.execute(text("UPDATE blood_units SET archived_at = now() WHERE din = :d"), {"d": self.din("GONE")})
        self.assertEqual(set(self.rows()), {self.din("LIVE"), self.din("EXPIRED")})

    def test_the_read_writes_no_expiry_notification(self):
        add(self.bank, self.din("NEAR"), 5)
        add(self.bank, self.din("FAR"), 60)
        app.get_inventory(facility_id=self.bank)
        app.get_inventory(facility_id=self.bank)
        self.assertEqual(q("SELECT count(*) FROM notifications WHERE facility_id = :f", f=self.bank)[0][0], 0)

    def test_clearing_the_backlog_archives_expired_units_but_not_ones_still_reserved(self):
        add(self.bank, self.din("OLD1"), -4)
        add(self.bank, self.din("OLD2"), -8)
        add(self.bank, self.din("HELD"), -2)
        add(self.bank, self.din("LIVE"), 20)
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, component, quantity, "
                                 "emergency_type, status) VALUES (:h, :b, 'O+', 'Packed RBC', 1, 'restock', 'accepted') RETURNING id"),
                            {"h": self.hospital, "b": self.bank}).scalar()
            c.execute(text("UPDATE blood_units SET reserved_for_request_id = :r WHERE din = :d"), {"r": rid, "d": self.din("HELD")})
        self.assertEqual(app.archive_expired_inventory(facility_id=self.bank), {"archived_count": 2})
        self.assertEqual(set(self.rows()), {self.din("HELD"), self.din("LIVE")})


if __name__ == "__main__":
    unittest.main()
