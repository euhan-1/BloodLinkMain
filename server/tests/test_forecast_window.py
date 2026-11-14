"""The forecast reads at most FORECAST_WINDOW_DAYS (730) days per blood type (_forecast_daily_series), and the upload cap is 15,000 entries.
The window is cut AFTER the fill, so what a window holds is what the full series held on those days. Scratch database for the get_forecast tests."""
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import text

import history_import as h
import main as app
from database import engine

TYPES = sorted(app.VALID_BLOOD_TYPES)
END = date(2026, 6, 30)
W = app.FORECAST_WINDOW_DAYS


def rows_for(n_days, skip=(), tag=1, end=END):
    """history rows as get_forecast reads them: eight types on each of the n_days days ending `end`; skip = {(day, type)}."""
    days = [end - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    return [{"snapshot_date": d, "blood_type": t, "units": 20 + (d.toordinal() * 7 + i * 5) % 40, "upload_history_id": tag}
            for d in days for i, t in enumerate(TYPES) if (d, t) not in skip]


def series_by_type(rows, fn, end=END):
    observed = {r["snapshot_date"] for r in rows}
    carry = app._carry_forward_dates(rows)
    by = {}
    for r in rows:
        by.setdefault(r["blood_type"], {})[r["snapshot_date"]] = r["units"]
    return {t: fn(own, observed, end, carry) for t, own in by.items()}


class WindowTests(unittest.TestCase):
    def test_the_window_is_730_days(self):
        self.assertEqual(W, 730)

    def test_a_400_day_and_a_730_day_series_are_exactly_what_they_were(self):
        for n in (400, 729, 730):
            for tag in (1, None):
                rows = rows_for(n, tag=tag)
                old = series_by_type(rows, app._resample_daily_series)
                new = series_by_type(rows, app._forecast_daily_series)
                self.assertEqual(new, old, (n, tag))
                self.assertTrue(all(len(s) == n for s in new.values()))

    def test_gaps_and_a_skipped_row_inside_730_days_change_nothing_either(self):
        days = [END - timedelta(days=i) for i in range(0, 700)]
        rows = [r for r in rows_for(700) if r["snapshot_date"].day not in (10, 11, 12) and not (r["snapshot_date"] == days[40] and r["blood_type"] == "O-")]
        self.assertEqual(series_by_type(rows, app._forecast_daily_series), series_by_type(rows, app._resample_daily_series))

    def test_a_series_over_730_days_is_cut_to_the_last_730_ending_today(self):
        for n in (731, 1000, 1800):
            rows = rows_for(n)
            full = series_by_type(rows, app._resample_daily_series)
            cut = series_by_type(rows, app._forecast_daily_series)
            for t in TYPES:
                self.assertEqual(len(full[t]), n)
                self.assertEqual(len(cut[t]), W)
                self.assertEqual(cut[t], full[t][-W:], (n, t))                    # the same values, only the older ones dropped
                self.assertEqual((cut[t][0][0], cut[t][-1][0]), (END - timedelta(days=W - 1), END))
                self.assertEqual([b[0] - a[0] for a, b in zip(cut[t], cut[t][1:])], [timedelta(days=1)] * (W - 1))  # still daily, no holes

    def test_the_first_day_of_the_window_carries_the_previous_days_value_not_a_zero(self):
        first = END - timedelta(days=W - 1)
        # O- is not recorded on the window's first day, nor on the three days before it; it last had 77 four days before the window
        skip = {(first - timedelta(days=k), "O-") for k in range(0, 4)}
        rows = rows_for(1200, skip=skip, tag=1)                                    # every day from an upload
        rows = [dict(r, units=77) if (r["blood_type"] == "O-" and r["snapshot_date"] == first - timedelta(days=4)) else r for r in rows]
        cut = dict(series_by_type(rows, app._forecast_daily_series)["O-"])
        self.assertEqual(cut[first], 77)                                           # carried from outside the window
        self.assertNotEqual(cut[first], 0)
        full = dict(series_by_type(rows, app._resample_daily_series)["O-"])
        self.assertEqual(cut[first], full[first])

    def test_on_a_day_the_app_recorded_a_missing_type_is_still_a_real_zero_at_the_window_start(self):
        first = END - timedelta(days=W - 1)
        rows = rows_for(1200, skip={(first, "O-")}, tag=None)                      # the app writes all eight types, so a gap is a zero
        self.assertEqual(dict(series_by_type(rows, app._forecast_daily_series)["O-"])[first], 0)

    def test_a_recorded_first_day_keeps_its_value(self):
        first = END - timedelta(days=W - 1)
        rows = [dict(r, units=55) if r["snapshot_date"] == first and r["blood_type"] == "A+" else r for r in rows_for(1000)]
        self.assertEqual(dict(series_by_type(rows, app._forecast_daily_series)["A+"])[first], 55)

    def test_the_chart_totals_use_the_same_window_and_give_the_same_last_90_days(self):
        rows = rows_for(1800)
        chart = app._history_totals(rows, END)
        self.assertEqual(len(chart), app.HISTORY_DAYS)
        full = series_by_type(rows, app._resample_daily_series)
        expect = {d: sum(dict(full[t])[d] for t in TYPES) for d in (END - timedelta(days=i) for i in range(app.HISTORY_DAYS))}
        self.assertEqual({p["date"]: p["units"] for p in chart}, {d.isoformat(): u for d, u in expect.items()})
        self.assertTrue(all(p["observed"] for p in chart))
        cut = series_by_type(rows, app._forecast_daily_series)          # and it is built from exactly what the fit is fed
        self.assertEqual({p["date"]: p["units"] for p in chart}, {d.isoformat(): sum(dict(cut[t])[d] for t in TYPES) for d in expect})


class GetForecastReadsTheWindowTests(unittest.TestCase):
    """GET /forecast end to end on the scratch database, with the fit itself replaced so only the series it would be given is seen."""

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) VALUES (:n, 'bloodbank', true, true) RETURNING id"),
                                 {"n": f"Window {uuid.uuid4().hex[:6]}"}).scalar()
        self.addCleanup(self.cleanup)
        self.today = app.business_today()

    def cleanup(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshots"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id=:f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id=:f"), {"f": self.fid})

    def history(self, days):
        """days of app-written history ending yesterday, eight types a day"""
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) "
                "SELECT :t - g, bt, 20 + ((g * 7 + length(bt) * 3) % 40), :f FROM generate_series(1, :n) g CROSS JOIN unnest(CAST(:types AS text[])) bt"),
                {"t": self.today, "f": self.fid, "n": days, "types": TYPES})

    def forecast(self):
        seen = {}

        def fake(fid, daily_by_type, today, observed_dates=None, prefetched_cache=None):
            seen.update(daily_by_type)
            return {bt: None for bt in daily_by_type}, []

        with mock.patch.object(app, "_collect_sarimax_results", fake):
            return app.get_forecast(facility_id=self.fid), seen

    def test_a_long_history_is_cut_for_the_fit_but_still_counts_as_history(self):
        self.history(1000)
        body, seen = self.forecast()
        self.assertEqual({t: len(s) for t, s in seen.items()}, {t: W for t in TYPES})
        self.assertEqual({(s[0][0], s[-1][0]) for s in seen.values()}, {(self.today - timedelta(days=W - 1), self.today)})
        self.assertEqual(body["days_of_history"], 1001)               # distinct recorded days over the whole history, today included
        self.assertEqual(len(body["history"]), app.HISTORY_DAYS)
        self.assertTrue(body["has_sufficient_history"])

    def test_a_history_inside_the_window_is_fed_whole(self):
        self.history(700)
        body, seen = self.forecast()
        self.assertEqual({t: len(s) for t, s in seen.items()}, {t: 701 for t in TYPES})  # first day through today, as before
        self.assertEqual(body["days_of_history"], 701)

    def test_the_30_day_rule_still_counts_the_whole_history(self):
        # 25 recent days and 600 old ones with a long hole between: 625 recorded days >= 30, so a real fit is attempted
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) "
                "SELECT :t - g, bt, 30, :f FROM (SELECT g FROM generate_series(1, 25) g UNION ALL SELECT g FROM generate_series(900, 1499) g) x "
                "CROSS JOIN unnest(CAST(:types AS text[])) bt"), {"t": self.today, "f": self.fid, "types": TYPES})
        body, seen = self.forecast()
        self.assertEqual(body["days_of_history"], 626)
        self.assertEqual(body["forecast_source"], "sarimax_facility_history")
        self.assertTrue(all(len(s) == W for s in seen.values()))


class UploadCapTests(unittest.TestCase):
    def csv(self, n):
        return "date,type,units\n" + "\n".join(f"2026-01-01,{TYPES[i % 8]},1" for i in range(n)) + "\n"

    def test_the_cap_is_15000_and_the_message_names_it(self):
        self.assertEqual(h.MAX_ENTRIES, 15_000)
        with self.assertRaises(h.HistoryFileError) as cm:
            h.parse_history_upload("csv", self.csv(15_001), date(2026, 11, 15))
        self.assertIn("more than 15,000 figures", str(cm.exception))

    def test_exactly_15000_is_accepted(self):
        p = h.parse_history_upload("csv", self.csv(15_000), date(2026, 11, 15))
        self.assertEqual(len(p.rows), 15_000)

    def test_the_message_follows_the_constant(self):
        with mock.patch.object(h, "MAX_ENTRIES", 1234):
            with self.assertRaises(h.HistoryFileError) as cm:
                h.parse_history_upload("csv", self.csv(1235), date(2026, 11, 15))
        self.assertIn("more than 1,234 figures", str(cm.exception))
