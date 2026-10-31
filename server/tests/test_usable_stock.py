"""One definition of "usable stock": USABLE_COUNTS_SQL.

/inventory/summary, the hospital's threshold view and the snapshot writer all count the same thing: a unit whose expires_date is today
or later, archived or not (an archived unit that has not expired is still stock), reserved or not. They used to carry three copies of
that SQL; they now join the one derived table, so they cannot drift. These tests prove the three agree on a facility holding every
awkward case, that each of them is built from the shared constant, and what the constant counts.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import inspect
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_inventory import TODAY, _add_unit, _cleanup_facility, _make_facility


class UsableStockTests(unittest.TestCase):
    def setUp(self):
        tag = uuid.uuid4().hex[:6]
        with engine.begin() as c:
            self.fid = _make_facility(c, f"__test_usable_{tag}__")
            self.other = _make_facility(c, f"__test_usable_other_{tag}__")
            for days in (30, 5, 0):                                   # live, near expiry, expiring today: all usable
                _add_unit(c, self.fid, TODAY + timedelta(days=days), "O+")
            _add_unit(c, self.fid, TODAY - timedelta(days=1), "O+")   # expired yesterday: not usable
            _add_unit(c, self.fid, TODAY - timedelta(days=40), "O+")  # long expired: not usable
            archived = _add_unit(c, self.fid, TODAY + timedelta(days=10), "O+")  # archived but unexpired: still stock
            c.execute(text("UPDATE blood_units SET archived_at = now() WHERE din = :d"), {"d": archived})
            _add_unit(c, self.fid, TODAY + timedelta(days=9), "A-")
            _add_unit(c, self.other, TODAY + timedelta(days=9), "O+")  # another facility's: never counted here
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            for f in (self.fid, self.other):
                _cleanup_facility(c, f)

    EXPECTED = {"O+": 4, "A-": 1}  # 30d, 5d, today, archived-but-live | one A-

    def test_the_summary_counts_it(self):
        got = {r["blood_type"]: r["units"] for r in app.get_inventory_summary(facility_id=self.fid)}
        self.assertEqual({k: v for k, v in got.items() if v}, self.EXPECTED)

    def test_the_hospital_threshold_view_counts_the_same(self):
        with engine.connect() as c:
            view = app._build_hospital_threshold_view(c, self.fid, False, TODAY)
        got = {t["blood_type"]: t["units"] for t in view["thresholds"]}
        self.assertEqual({k: v for k, v in got.items() if v}, self.EXPECTED)

    def test_the_snapshot_writer_records_the_same(self):
        with engine.begin() as c:
            app._write_inventory_snapshots(c, TODAY, self.fid)
        with engine.connect() as c:
            got = {bt: u for bt, u in c.execute(
                text("SELECT blood_type, units FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d"), {"f": self.fid, "d": TODAY})}
        self.assertEqual({k: v for k, v in got.items() if v}, self.EXPECTED)
        self.assertEqual(len(got), 8, "all eight types, an empty one as an explicit 0")

    def test_the_snapshot_writer_for_every_facility_does_not_mix_them_up(self):
        with engine.begin() as c:
            app._write_inventory_snapshots(c, TODAY)  # facility_id None: everyone
        with engine.connect() as c:
            mine = {bt: u for bt, u in c.execute(text("SELECT blood_type, units FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d"),
                                                 {"f": self.fid, "d": TODAY})}
            theirs = {bt: u for bt, u in c.execute(text("SELECT blood_type, units FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d"),
                                                   {"f": self.other, "d": TODAY})}
        self.assertEqual({k: v for k, v in mine.items() if v}, self.EXPECTED)
        self.assertEqual({k: v for k, v in theirs.items() if v}, {"O+": 1})

    def test_all_three_are_built_from_the_one_constant(self):
        for fn in (app.get_inventory_summary, app._build_hospital_threshold_view, app._write_inventory_snapshots):
            self.assertIn("USABLE_COUNTS_SQL", inspect.getsource(fn), fn.__name__)
            self.assertIn("({USABLE_COUNTS_SQL})", inspect.getsource(fn), f"{fn.__name__} joins it as a derived table")
            self.assertNotIn("FROM blood_units", inspect.getsource(fn), f"{fn.__name__} carries its own copy of the count")

    def test_the_constant_is_what_the_comment_says(self):
        self.assertIn("expires_date >= :today", app.USABLE_COUNTS_SQL)
        self.assertNotIn("archived_at", app.USABLE_COUNTS_SQL, "an archived unit that has not expired is still stock")
        self.assertNotIn("reserved", app.USABLE_COUNTS_SQL, "a unit reserved for a transfer is still on the shelf until it moves")
