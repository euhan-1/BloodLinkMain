"""GET /forecast's `history` (90 days of observed-or-carried-forward totals for the trend chart) and the real
dates on the forward series. The pure helper is tested on fabricated rows; the endpoint tests use a real DB
(scratch) with the SARIMAX fit stubbed, so this runs in seconds.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_forecast_history.py -q
"""
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import upload
from tests.test_forecast_incremental import TODAY, TYPES, stub_result

D0 = date(2026, 10, 29)


def rows(*entries):
    return [{"snapshot_date": d, "blood_type": t, "units": u} for d, t, u in entries]


def fed_totals(history_rows, today):
    """What a fit is fed, summed over types: the sum of each type's _resample_daily_series."""
    observed = {r["snapshot_date"] for r in history_rows}
    totals = {}
    for bt in {r["blood_type"] for r in history_rows}:
        own = {r["snapshot_date"]: r["units"] for r in history_rows if r["blood_type"] == bt}
        for d, u in app._resample_daily_series(own, observed, today):
            totals[d] = totals.get(d, 0) + u
    return totals


class HistoryTotalsTests(unittest.TestCase):
    def hole_rows(self):
        # days -9..-6 recorded, -5..-3 NOT recorded for any type, -2..0 recorded
        out = []
        for i, a, o in ((9, 10, 5), (8, 11, 5), (7, 12, 6), (6, 13, 6), (2, 20, 7), (1, 21, 7), (0, 22, 8)):
            out += [(D0 - timedelta(days=i), "A+", a), (D0 - timedelta(days=i), "O+", o)]
        return rows(*out)

    def test_the_observed_flag_and_filled_totals_across_a_known_hole(self):
        h = app._history_totals(self.hole_rows(), D0)
        by_date = {p["date"]: p for p in h}
        self.assertEqual(len(h), 10)  # first recorded day .. today, one point per day
        for i in (9, 8, 7, 6, 2, 1, 0):
            self.assertTrue(by_date[(D0 - timedelta(days=i)).isoformat()]["observed"], i)
        for i in (5, 4, 3):  # the hole: shown, flagged, carrying the last recorded total (13 + 6)
            p = by_date[(D0 - timedelta(days=i)).isoformat()]
            self.assertEqual((p["observed"], p["units"]), (False, 19), i)
        self.assertEqual(by_date[(D0 - timedelta(days=2)).isoformat()]["units"], 27)
        self.assertEqual(h[-1], {"date": D0.isoformat(), "units": 30, "observed": True})

    def test_totals_are_exactly_what_the_model_is_fed(self):
        r = self.hole_rows()
        fed = fed_totals(r, D0)
        self.assertEqual({p["date"]: p["units"] for p in app._history_totals(r, D0)}, {d.isoformat(): u for d, u in fed.items()})

    def test_a_blood_type_missing_on_a_recorded_day_is_a_real_zero_not_a_gap(self):
        r = rows((D0 - timedelta(days=1), "A+", 10), (D0 - timedelta(days=1), "O+", 4), (D0, "A+", 11))  # O+ has no row today
        h = {p["date"]: p for p in app._history_totals(r, D0)}
        self.assertEqual(h[D0.isoformat()], {"date": D0.isoformat(), "units": 11, "observed": True})  # 11 + a real 0, not 11 + 4 carried

    def test_only_the_last_90_days_are_returned(self):
        r = rows(*[(D0 - timedelta(days=i), "A+", 50) for i in range(200)])
        h = app._history_totals(r, D0)
        self.assertEqual((len(h), h[0]["date"], h[-1]["date"]), (90, (D0 - timedelta(days=89)).isoformat(), D0.isoformat()))

    def test_a_sparse_facility_is_mostly_filled(self):
        r = rows((D0 - timedelta(days=39), "A+", 8), (D0 - timedelta(days=20), "A+", 9), (D0 - timedelta(days=3), "A+", 7), (D0, "A+", 6))
        h = app._history_totals(r, D0)
        self.assertEqual((len(h), sum(p["observed"] for p in h)), (40, 4))
        self.assertEqual(h[1], {"date": (D0 - timedelta(days=38)).isoformat(), "units": 8, "observed": False})

    def test_no_rows_no_history(self):
        self.assertEqual(app._history_totals([], D0), [])


class HistoryInForecastTests(unittest.TestCase):
    """The endpoint itself. 40 days uploaded for 5 types, minus a 5-day hole (days -14..-10)."""

    HOLE = range(10, 15)

    def make_facility(self, days, hole=()):
        with engine.begin() as c:
            fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                 "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Hist {uuid.uuid4().hex[:6]}"}).scalar()
        self.fids.append(fid)
        if days:
            csv = ["snapshot_date,blood_type,units"] + [
                f"{TODAY - timedelta(days=d)},{t},{100 + (d * 7) % 5}" for d in range(1, days + 1) if d not in hole for t in TYPES]
            body, _, _ = upload(app.upload_historical_inventory_snapshots, fid, "\n".join(csv))
            self.assertEqual(body["errors"], [])
        return fid

    def setUp(self):
        self.fids = []

    def tearDown(self):
        with engine.begin() as c:
            for fid in self.fids:
                for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log",
                          "blood_units", "inventory_snapshots", "upload_history"):
                    c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": fid})
                c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": fid})
        app._SARIMAX_FIT_FAILED.clear()

    def test_history_flags_the_hole_and_ends_today_on_the_sarimax_path(self):
        fid = self.make_facility(40, self.HOLE)
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result):
            r = app.get_forecast(facility_id=fid)
        self.assertEqual(r["forecast_source"], "sarimax_facility_history")
        h = {p["date"]: p for p in r["history"]}
        self.assertEqual(len(h), 41)  # 40 days back + today
        for d in range(1, 41):
            p = h[(TODAY - timedelta(days=d)).isoformat()]
            self.assertEqual(p["observed"], d not in self.HOLE, d)
        # a hole day carries the last recorded value (day 15: 100 + 105 % 5 = 100 per type, five types)
        self.assertEqual(h[(TODAY - timedelta(days=12)).isoformat()]["units"], 100 * len(TYPES))
        self.assertEqual(r["history"][-1]["date"], TODAY.isoformat())
        self.assertTrue(r["history"][-1]["observed"])

    def test_history_is_present_while_types_are_pending_and_series_is_empty(self):
        fid = self.make_facility(40)
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result):
            r = app.get_forecast(facility_id=fid)  # at most 2 fits per request, five types need fitting
        self.assertTrue(r["pending_types"])
        self.assertEqual(r["series"], [])
        self.assertEqual(len(r["history"]), 41)

    def test_history_totals_match_what_the_fitter_was_actually_fed(self):
        fid = self.make_facility(40, self.HOLE)
        fed = {}

        def record(daily_series, today, **kw):
            fed[kw["log_prefix"].split()[-1]] = daily_series
            return stub_result(model_label=kw["model_label"])

        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=record):
            for _ in range(4):
                r = app.get_forecast(facility_id=fid)
                if not r["pending_types"]:
                    break
        self.assertEqual(set(fed), set(TYPES))
        for p in r["history"]:
            d = date.fromisoformat(p["date"])
            self.assertEqual(p["units"], sum(dict(series).get(d, 0) for series in fed.values()), p["date"])

    def test_forward_series_carries_real_dates_and_keeps_its_labels(self):
        fid = self.make_facility(40)
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result):
            for _ in range(4):
                r = app.get_forecast(facility_id=fid)
                if not r["pending_types"]:
                    break
        self.assertEqual([p["day"] for p in r["series"]], ["Today"] + [f"Day {c}" for c in app.FORECAST_CHECKPOINTS[1:]])
        self.assertEqual([p["date"] for p in r["series"]], [(TODAY + timedelta(days=c)).isoformat() for c in app.FORECAST_CHECKPOINTS])
        self.assertEqual(r["series"][0]["date"], r["history"][-1]["date"])  # day 0 is the last history point's date

    def test_the_linear_trend_path_has_history_too(self):
        fid = self.make_facility(10)
        r = app.get_forecast(facility_id=fid)
        self.assertEqual(r["forecast_source"], "linear_trend")
        self.assertEqual((len(r["history"]), r["history"][-1]["date"], all(p["observed"] for p in r["history"])), (11, TODAY.isoformat(), True))
        self.assertEqual(r["series"][0]["date"], TODAY.isoformat())

    def test_a_facility_with_no_forecast_at_all_still_gets_its_history(self):
        fid = self.make_facility(0)  # only today's snapshot, written by the call itself
        r = app.get_forecast(facility_id=fid)
        self.assertEqual((r["forecast_source"], r["series"]), ("none", []))
        self.assertEqual(r["history"], [{"date": TODAY.isoformat(), "units": 0, "observed": True}])


if __name__ == "__main__":
    unittest.main()
