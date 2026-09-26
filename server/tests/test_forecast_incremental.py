"""GET /forecast fits a bounded number of types per request, commits each type as it
finishes, serialises fitting per facility with an advisory lock, and reports what is
still pending. Real DB, disposable facility; the SARIMAX fit itself is stubbed (its
numerics are covered elsewhere), so this runs in seconds.

Run from server/: .venv\\Scripts\\python.exe -m unittest tests.test_forecast_incremental -v
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
TYPES = ["O+", "O-", "A+", "A-", "B+"]


class Killed(BaseException):
    """Stands in for the process/request dying mid-fit (not an Exception, so nothing swallows it)."""


def stub_result(*_):
    return {"checkpoints": {c: {"units": 50, "lower": 40, "upper": 60} for c in app.FORECAST_CHECKPOINTS},
            "exog_included": True, "model_order": app.FACILITY_SARIMAX_LABEL}


class IncrementalForecastTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Incr {uuid.uuid4().hex[:6]}"}).scalar()
        rows = ["snapshot_date,blood_type,units"] + [
            f"{TODAY - timedelta(days=d)},{t},{100 + (d * 7) % 5}" for d in range(1, 41) for t in TYPES]
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(rows))
        self.assertEqual(body["errors"], [])

    def tearDown(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log",
                      "blood_units", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})
        app._SARIMAX_FIT_FAILED.clear()

    def cached_types(self):
        return sorted({r[0] for r in q("SELECT blood_type FROM facility_forecast_cache WHERE facility_id=:f", f=self.fid)})

    def lock_is_free(self):
        with engine.connect() as c:
            got = c.execute(text("SELECT pg_try_advisory_lock(:n,:f)"), {"n": app._FORECAST_LOCK_NAMESPACE, "f": self.fid}).scalar()
            if got:
                c.execute(text("SELECT pg_advisory_unlock(:n,:f)"), {"n": app._FORECAST_LOCK_NAMESPACE, "f": self.fid})
            return got

    def test_at_most_two_fits_per_request_and_the_panel_completes(self):
        fit = mock.Mock(side_effect=stub_result)
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", fit):
            r1 = app.get_forecast(facility_id=self.fid)
            self.assertEqual(fit.call_count, 2)
            self.assertEqual(len(r1["pending_types"]), 3)
            self.assertEqual(r1["series"], [], "no facility total while types are missing")
            self.assertEqual(r1["restock_status"], "pending")
            self.assertEqual(r1["types_total"], 5)
            self.assertTrue(self.lock_is_free(), "the advisory lock must be released after the request")

            r2 = app.get_forecast(facility_id=self.fid)
            self.assertEqual((fit.call_count, len(r2["pending_types"])), (4, 1))
            r3 = app.get_forecast(facility_id=self.fid)
            self.assertEqual((fit.call_count, r3["pending_types"]), (5, []))
            self.assertEqual(len(r3["series"]), 7)
            self.assertEqual(r3["series"][-1]["units"], 5 * 50)
            app.get_forecast(facility_id=self.fid)
            self.assertEqual(fit.call_count, 5, "a complete cache means no more fits")

    def test_a_request_killed_partway_keeps_the_types_it_finished(self):
        calls = {"n": 0}

        def dies_on_second(*a):
            calls["n"] += 1
            if calls["n"] == 2:
                raise Killed()
            return stub_result()

        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=dies_on_second):
            with self.assertRaises(Killed):
                app.get_forecast(facility_id=self.fid)
        self.assertEqual(len(self.cached_types()), 1, "the finished type is committed, not rolled back with the request")
        self.assertTrue(self.lock_is_free(), "a dying request must still release the advisory lock")
        ids_before = q("SELECT id FROM facility_forecast_cache WHERE facility_id=:f ORDER BY id", f=self.fid)

        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result) as fit:
            app.get_forecast(facility_id=self.fid)
        self.assertEqual(fit.call_count, 2)
        self.assertEqual(q("SELECT id FROM facility_forecast_cache WHERE facility_id=:f AND id = ANY(:i)", f=self.fid,
                           i=[r[0] for r in ids_before]), ids_before, "the already-cached type must not be refit")
        self.assertEqual(len(self.cached_types()), 3)

    def test_second_request_while_another_is_fitting_fits_nothing_and_does_not_error(self):
        holder = engine.connect()
        try:
            holder.execute(text("SELECT pg_advisory_lock(:n,:f)"), {"n": app._FORECAST_LOCK_NAMESPACE, "f": self.fid})
            with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result) as fit:
                r = app.get_forecast(facility_id=self.fid)
            self.assertEqual(fit.call_count, 0)
            self.assertEqual(len(r["pending_types"]), 5)
            self.assertEqual(r["forecast_source"], "sarimax_facility_history")
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:n,:f)"), {"n": app._FORECAST_LOCK_NAMESPACE, "f": self.fid})
            holder.close()

    def test_cache_write_is_an_upsert(self):
        series = [(TODAY - timedelta(days=d), 50) for d in range(40, -1, -1)]
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result):
            app._fit_and_cache_sarimax(self.fid, "O+", series, TODAY)
            app._fit_and_cache_sarimax(self.fid, "O+", series, TODAY)  # second writer: must not raise IntegrityError
        self.assertEqual(q("SELECT count(*) FROM facility_forecast_cache WHERE facility_id=:f AND blood_type='O+'", f=self.fid)[0][0], 7)

    def test_pending_types_keep_their_alert_state(self):
        with engine.begin() as c:
            for t in TYPES:
                c.execute(text("INSERT INTO forecast_alert_state (facility_id, blood_type, alerting) VALUES (:f,:b,true)"), {"f": self.fid, "b": t})
                app._create_notification(c, self.fid, "forecast_shortage", f"{t} {app._SHORTAGE_MSG_TAIL} 9 days", "dashboard")
        with mock.patch.object(app, "MAX_SARIMAX_FITS_PER_REQUEST", 0):
            r = app.get_forecast(facility_id=self.fid)
        self.assertEqual(len(r["pending_types"]), 5)
        self.assertEqual(q("SELECT count(*) FROM forecast_alert_state WHERE facility_id=:f AND alerting", f=self.fid)[0][0], 5,
                         "types not evaluated yet must not be read as 'no longer alerting'")
        self.assertEqual(q("SELECT count(*) FROM notifications WHERE facility_id=:f AND read_at IS NULL", f=self.fid)[0][0], 5)

    def test_unusable_fit_falls_back_to_linear_and_is_not_retried_every_poll(self):
        fit = mock.Mock(return_value=None)
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", fit), mock.patch.object(app, "MAX_SARIMAX_FITS_PER_REQUEST", 5):
            r1 = app.get_forecast(facility_id=self.fid)
            self.assertEqual(r1["pending_types"], [])
            self.assertEqual(sorted(r1["sarimax_fallback_types"]), sorted(TYPES))
            self.assertEqual(fit.call_count, 5)
            app.get_forecast(facility_id=self.fid)
            self.assertEqual(fit.call_count, 5, "a type that failed today is not refit on every request")


if __name__ == "__main__":
    unittest.main()
