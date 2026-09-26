"""Historical-upload undo vs the forecast cache and alert state, against the real DB
on disposable bloodbank facilities that are deleted afterwards.

Covers: cache invalidated by a same-day corrected re-upload / an undo / a changed
today's stock; undo restoring a snapshot the upload overwrote; undo of overlapping
uploads in either order; the "none" forecast path clearing alert state; and a
cleared alert resolving its bell notification.

Run from server/: .venv\\Scripts\\python.exe -m unittest tests.test_snapshot_undo_and_cache -v
"""
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import q, upload

TODAY = date.today()


def day(n):
    """n days before today."""
    return TODAY - timedelta(days=n)


def csv_for(btype, days_and_units):
    return "snapshot_date,blood_type,units\n" + "\n".join(f"{day(d)},{btype},{u}" for d, u in days_and_units)


def snaps(fid, btype="O+"):
    return {r[0]: (r[1], r[2]) for r in q(
        "SELECT snapshot_date, units, upload_history_id FROM inventory_snapshots WHERE facility_id=:f AND blood_type=:b", f=fid, b=btype)}


class SnapshotUndoAndCacheTests(unittest.TestCase):
    def setUp(self):
        tag = uuid.uuid4().hex[:6]
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"UndoTest {tag}"}).scalar()

    def tearDown(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log",
                      "blood_units", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def hist_upload(self, csv_text):
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, csv_text)
        self.assertEqual(body["errors"], [])
        return q("SELECT max(id) FROM upload_history WHERE facility_id=:f AND upload_type='historical_stock'", f=self.fid)[0][0]

    def cache_ids(self):
        return sorted(r[0] for r in q("SELECT id FROM facility_forecast_cache WHERE facility_id=:f", f=self.fid))

    # ---- Fix 1 -----------------------------------------------------------
    def test_corrected_same_day_reupload_replaces_the_cached_forecast(self):
        wobble = [0, 3, -2, 5, -4, 1, 2]
        v1 = [(d, 100 + wobble[d % 7]) for d in range(1, 41)]
        v2 = [(d, 300 + wobble[d % 7]) for d in range(1, 41)]
        self.hist_upload(csv_for("O+", v1))
        first = app.get_forecast(facility_id=self.fid)
        self.assertEqual(first["forecast_source"], "sarimax_facility_history")
        self.assertTrue(self.cache_ids(), "SARIMAX fit should be cached")

        self.hist_upload(csv_for("O+", v2))  # corrected export, same day
        self.assertEqual(self.cache_ids(), [], "re-upload must drop the stale cache in the same transaction")
        second = app.get_forecast(facility_id=self.fid)
        self.assertGreater(second["series"][-1]["units"], first["series"][-1]["units"] + 100)

    def test_undo_drops_the_cache_and_falls_back_below_the_sarimax_threshold(self):
        wobble = [0, 3, -2, 5, -4, 1, 2]
        up = self.hist_upload(csv_for("O+", [(d, 100 + wobble[d % 7]) for d in range(1, 41)]))
        self.assertEqual(app.get_forecast(facility_id=self.fid)["forecast_source"], "sarimax_facility_history")
        self.assertTrue(self.cache_ids())
        app.undo_upload(up, facility_id=self.fid)
        self.assertEqual(self.cache_ids(), [])
        after = app.get_forecast(facility_id=self.fid)
        self.assertNotEqual(after["forecast_source"], "sarimax_facility_history")
        self.assertEqual(after["days_of_history"], 0)

    def test_stock_change_today_updates_day_0_without_refit_and_unchanged_read_hits_cache(self):
        wobble = [0, 3, -2, 5, -4, 1, 2]
        self.hist_upload(csv_for("O+", [(d, 100 + wobble[d % 7]) for d in range(1, 41)]))
        exp = TODAY + timedelta(days=30)
        ins = ("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
               "VALUES (:a,:b,:c,:d,:e,:f,:g,:h)")
        def add_unit(n):
            with engine.begin() as c:
                c.execute(text(ins), dict(zip("abcdefgh", (
                    f"UT{self.fid}-{n}", "O+", "Packed RBC", "Bay", 280, TODAY - timedelta(days=2), exp, self.fid))))

        add_unit(1)
        first = app.get_forecast(facility_id=self.fid)
        ids = self.cache_ids()
        self.assertTrue(ids)
        self.assertEqual(first["series"][0]["units"], 1)
        again = app.get_forecast(facility_id=self.fid)
        self.assertEqual(self.cache_ids(), ids, "unchanged stock must keep serving the cache")
        self.assertEqual(again["series"], first["series"])

        add_unit(2)
        after = app.get_forecast(facility_id=self.fid)
        self.assertEqual(self.cache_ids(), ids, "a stock change must NOT trigger a refit")
        self.assertEqual(after["series"][0]["units"], 2, "day 0 must reflect live stock immediately")
        self.assertEqual(after["series"][1:], first["series"][1:], "days 5-30 stay as fitted")

    # ---- Fix 2 -----------------------------------------------------------
    def test_undo_restores_the_snapshot_the_upload_overwrote(self):
        with engine.begin() as c:  # an organic snapshot from before the upload
            c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) VALUES (:d,'O+',77,:f)"),
                      {"d": day(3), "f": self.fid})
        up = self.hist_upload(csv_for("O+", [(3, 5), (4, 6), (5, 7)]))
        self.assertEqual(snaps(self.fid)[day(3)], (5, up))
        result = app.undo_upload(up, facility_id=self.fid)
        self.assertEqual(result["removed_count"], 3)  # 1 restored + 2 deleted
        self.assertEqual(snaps(self.fid), {day(3): (77, None)})

    def test_undo_a_then_b_after_overlap(self):
        a = self.hist_upload(csv_for("O+", [(d, 10) for d in (8, 7, 6, 5, 4)]))  # days 8..4
        b = self.hist_upload(csv_for("O+", [(d, 20) for d in (5, 4, 3, 2, 1)]))  # days 5..1, overlaps 5,4
        self.assertEqual(snaps(self.fid)[day(5)], (20, b))
        app.undo_upload(a, facility_id=self.fid)
        # A's exclusive rows go; the overlap keeps B's (still live) values
        self.assertEqual(snaps(self.fid), {day(d): (20, b) for d in (5, 4, 3, 2, 1)})
        app.undo_upload(b, facility_id=self.fid)
        self.assertEqual(snaps(self.fid), {}, "B undone with A already undone must leave nothing behind")

    def test_undo_b_then_a_after_overlap_restores_a_values(self):
        a = self.hist_upload(csv_for("O+", [(d, 10) for d in (8, 7, 6, 5, 4)]))
        b = self.hist_upload(csv_for("O+", [(d, 20) for d in (5, 4, 3, 2, 1)]))
        app.undo_upload(b, facility_id=self.fid)
        self.assertEqual(snaps(self.fid), {day(d): (10, a) for d in (8, 7, 6, 5, 4)})
        app.undo_upload(a, facility_id=self.fid)
        self.assertEqual(snaps(self.fid), {})

    # ---- Fix 3 -----------------------------------------------------------
    def _alerting(self, btype):
        r = q("SELECT alerting FROM forecast_alert_state WHERE facility_id=:f AND blood_type=:b", f=self.fid, b=btype)
        return r[0][0] if r else None

    def _seed_alert(self, btype):
        with engine.begin() as c:
            c.execute(text("INSERT INTO forecast_alert_state (facility_id, blood_type, alerting) VALUES (:f,:b,true)"), {"f": self.fid, "b": btype})
            app._create_notification(c, self.fid, "forecast_shortage", f"{btype} {app._SHORTAGE_MSG_TAIL} 9 days", "dashboard")

    def _unread(self, btype):
        return q("SELECT count(*) FROM notifications WHERE facility_id=:f AND type='forecast_shortage' AND message LIKE :p AND read_at IS NULL",
                 f=self.fid, p=f"{btype} %")[0][0]

    def test_none_path_clears_alert_state_and_resolves_notifications(self):
        self._seed_alert("O+")
        self._seed_alert("A+")
        with mock.patch.object(app, "_build_synthetic_stand_in_forecast", return_value=None):
            body = app.get_forecast(facility_id=self.fid)
        self.assertEqual(body["forecast_source"], "none")
        self.assertEqual((self._alerting("O+"), self._alerting("A+")), (False, False))
        self.assertEqual((self._unread("O+"), self._unread("A+")), (0, 0))

    def test_cleared_alert_resolves_only_its_own_notification(self):
        self._seed_alert("A+")
        self._seed_alert("AB+")
        with engine.begin() as c:  # AB+ is still alerting, A+ no longer is
            app._check_forecast_shortage_notifications(c, self.fid, [{"type": "AB+", "days_until_threshold": 9}])
        self.assertEqual((self._alerting("A+"), self._alerting("AB+")), (False, True))
        self.assertEqual((self._unread("A+"), self._unread("AB+")), (0, 1))  # "A+ " prefix must not catch "AB+"


if __name__ == "__main__":
    unittest.main()
