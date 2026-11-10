"""WARM_FORECASTS_AFTER_SNAPSHOT: after the nightly snapshot job answers, a background task loads every bank's forecast through
get_forecast itself, so a user's first load of the day is warm. These tests prove it is off by default, only runs after a
authorised job, answers the cron call the same either way, goes through the endpoint's own function (so the lock, the 2-fit cap, the
snapshot write and the notifications are the endpoint's), and leaves the next user load with nothing to fit.

Needs the scratch database from conftest.py (TEST_DATABASE_URL)."""
import contextlib
import io
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair

SECRET = "test-cron-secret-not-a-real-one"
TYPES = sorted(app.VALID_BLOOD_TYPES)
LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue [grid-searched]"


class SwitchAndSchedulingTests(unittest.TestCase):
    def job(self, bg, secret=SECRET):
        with mock.patch.dict("os.environ", {"CRON_SECRET": SECRET}):
            return app.daily_snapshot(x_cron_secret=secret, background_tasks=bg)

    def test_the_switch_defaults_to_off_and_only_an_explicit_true_turns_it_on(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertFalse(app._warm_forecasts_enabled())
        for value, expected in (("true", True), ("TRUE", True), (" 1 ", True), ("yes", True), ("on", True),
                                ("false", False), ("", False), ("0", False), ("no", False), ("maybe", False)):
            with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": value}):
                self.assertEqual(app._warm_forecasts_enabled(), expected, repr(value))

    def test_off_nothing_is_scheduled(self):
        bg = BackgroundTasks()
        with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": "false"}):
            self.job(bg)
        self.assertEqual(len(bg.tasks), 0)

    def test_on_exactly_one_task_is_scheduled_and_it_is_not_run_inside_the_request(self):
        bg = BackgroundTasks()
        with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": "true"}), \
                mock.patch.object(app, "_warm_all_forecasts") as warm:
            self.job(bg)
            warm.assert_not_called()                     # the cron request is answered first
            self.assertEqual([t.func for t in bg.tasks], [warm])
            self.assertEqual(bg.tasks[0].args, ())

    def test_the_cron_response_is_identical_on_or_off(self):
        results = []
        for value in ("false", "true"):
            with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": value}):
                results.append(self.job(BackgroundTasks()))
        for r in results:
            self.assertEqual(set(r), {"snapshot_date", "facilities", "rows_inserted", "rows_updated", "archived_expired", "expired_still_reserved"})
        self.assertEqual(results[0]["facilities"], results[1]["facilities"])

    def test_a_wrong_or_missing_secret_schedules_nothing(self):
        for secret in ("wrong", None):
            bg = BackgroundTasks()
            with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": "true"}), self.assertRaises(HTTPException) as ctx:
                self.job(bg, secret=secret)
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(len(bg.tasks), 0)

    def test_a_direct_call_without_background_tasks_still_works(self):
        with mock.patch.dict("os.environ", {"WARM_FORECASTS_AFTER_SNAPSHOT": "true"}):
            self.assertIn("snapshot_date", self.job(None))


class FakeFit:
    """A stand-in for _fit_and_cache_sarimax: records each call, returns a result shaped like a real fit, and caches it the way the real
    one does (same table, same rows) so the next call sees that type as done."""

    def __init__(self, declining_type=None):
        self.calls, self.declining_type = [], declining_type

    def __call__(self, facility_id, blood_type, daily_series, today, observed_dates=None):
        self.calls.append(blood_type)
        slope = 9 if blood_type == self.declining_type else 0
        result = {"checkpoints": {c: {"units": 50 - slope * c // 5, "lower": 40 - slope * c // 5, "upper": 60 - slope * c // 5}
                                  for c in app.FORECAST_CHECKPOINTS},
                  "exog_included": True, "model_order": LABEL}
        with engine.begin() as c:
            for cp, v in result["checkpoints"].items():
                c.execute(text("INSERT INTO facility_forecast_cache (facility_id, blood_type, forecast_date, forecast_units, lower_units, "
                               "upper_units, trained_through_date, model_order) VALUES (:f, :b, :d, :u, :l, :h, :t, :m) "
                               "ON CONFLICT (facility_id, blood_type, forecast_date) DO UPDATE SET forecast_units = EXCLUDED.forecast_units, "
                               "lower_units = EXCLUDED.lower_units, upper_units = EXCLUDED.upper_units, trained_through_date = EXCLUDED.trained_through_date"),
                          {"f": facility_id, "b": blood_type, "d": today + timedelta(days=cp), "u": v["units"], "l": v["lower"], "h": v["upper"],
                           "t": today, "m": LABEL})
        return result


class Seeded(unittest.TestCase):
    def make_bank(self, days=40, types=TYPES, active=True):
        tag = uuid.uuid4().hex[:6].upper()
        bank, _ = make_pair(self, tag)
        with engine.begin() as c:
            c.execute(text("UPDATE facilities SET is_active = :a WHERE id = :f"), {"a": active, "f": bank})
            for bt in TYPES:
                c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, :b, 10, 100)"),
                          {"f": bank, "b": bt})
            for n in range(days):
                for bt in types:
                    c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) VALUES (:d, :b, 50, :f)"),
                              {"d": TODAY - timedelta(days=days - 1 - n), "b": bt, "f": bank})
        self.addCleanup(self.clean, bank)
        return bank

    def clean(self, bank):
        app._SARIMAX_FIT_FAILED.clear()
        with engine.begin() as c:
            for t in ("facility_forecast_cache", "forecast_alert_state", "notifications", "inventory_snapshots", "blood_type_thresholds"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": bank})

    def forecast(self, bank, **patches):
        with mock.patch.object(app, "business_today", return_value=TODAY):
            return app.get_forecast(facility_id=bank)

    def notifications(self, bank):
        with engine.connect() as c:
            return [r[0] for r in c.execute(text("SELECT message FROM notifications WHERE facility_id = :f AND type = 'forecast_shortage' ORDER BY id"), {"f": bank})]


class WhichBanksTests(Seeded):
    def test_only_active_banks_with_enough_history_are_warmed(self):
        long_bank = self.make_bank(days=40)
        short_bank = self.make_bank(days=10)
        inactive = self.make_bank(days=40, active=False)
        hospital = make_pair(self, uuid.uuid4().hex[:6].upper())[1]
        with engine.begin() as c:   # a hospital with plenty of history is still never forecast
            for n in range(40):
                c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) VALUES (:d, 'O+', 5, :f)"),
                          {"d": TODAY - timedelta(days=n), "f": hospital})
        self.addCleanup(self.clean, hospital)
        warmed = app._facilities_to_warm()
        self.assertIn(long_bank, warmed)
        for excluded in (short_bank, inactive, hospital):
            self.assertNotIn(excluded, warmed)
        self.assertEqual(warmed, sorted(warmed))


class OneFacilityTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, answers):
        with mock.patch.object(app, "get_forecast", side_effect=[{"pending_types": a} for a in answers]) as gf:
            summary = app._warm_one_facility(7)
        return summary, gf

    def test_it_loads_the_forecast_through_the_endpoints_own_function_until_nothing_is_pending(self):
        summary, gf = self.run_with([["A+", "B+", "O+"], ["O+"], []])
        self.assertEqual(gf.call_count, 3)
        for call in gf.call_args_list:
            self.assertEqual(call.kwargs, {"facility_id": 7})          # exactly the call a dashboard load makes
        self.assertEqual(summary, "complete after 3 calls")
        self.assertEqual(self.sleep.call_count, 2)                      # a pause between calls, none after the last

    def test_a_warm_bank_costs_one_call(self):
        summary, gf = self.run_with([[]])
        self.assertEqual((gf.call_count, summary), (1, "complete after 1 call"))
        self.sleep.assert_not_called()

    def test_a_round_with_no_progress_stops_it_because_another_request_holds_the_fit_lock(self):
        summary, gf = self.run_with([["O+", "A+"], ["O+", "A+"], ["O+"]])
        self.assertEqual(gf.call_count, 2)
        self.assertIn("another request is fitting", summary)

    def test_it_never_loops_past_the_bound(self):
        endless = [[f"T{i}"] for i in range(20)]                        # always different, never empty
        summary, gf = self.run_with(endless)
        self.assertEqual(gf.call_count, app._warm_max_rounds())
        self.assertEqual(app._warm_max_rounds(), 5)                     # ceil(8 types / 2 fits) + 1
        self.assertIn("stopped after 5 calls", summary)


class AllFacilitiesTests(unittest.TestCase):
    def test_one_banks_failure_is_logged_and_the_next_bank_still_runs(self):
        log = io.StringIO()
        with mock.patch.object(app, "_facilities_to_warm", return_value=[1, 2, 3]), \
                mock.patch.object(app, "_warm_one_facility", side_effect=[RuntimeError("boom"), "complete after 1 call", "complete after 1 call"]) as one, \
                contextlib.redirect_stdout(log):
            app._warm_all_forecasts()
        self.assertEqual(one.call_count, 3)
        self.assertIn("[warm] facility 1: failed, RuntimeError: boom", log.getvalue())
        self.assertIn("[warm] facility 3: complete after 1 call", log.getvalue())
        self.assertIn("[warm] done in", log.getvalue())

    def test_failing_to_list_the_banks_is_logged_not_raised(self):
        log = io.StringIO()
        with mock.patch.object(app, "_facilities_to_warm", side_effect=RuntimeError("db down")), contextlib.redirect_stdout(log):
            app._warm_all_forecasts()
        self.assertIn("could not list the banks to warm", log.getvalue())

    def test_the_log_names_the_date_it_is_warming(self):
        log = io.StringIO()
        with mock.patch.object(app, "_facilities_to_warm", return_value=[]), mock.patch.object(app, "business_today", return_value=TODAY), \
                contextlib.redirect_stdout(log):
            app._warm_all_forecasts()
        self.assertIn(f"warming 0 bank(s) for {TODAY.isoformat()}", log.getvalue())


class RealPathTests(Seeded):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_cold_bank_is_warmed_by_real_fits_and_the_next_user_load_fits_nothing(self):
        bank = self.make_bank(days=45, types=["A+", "B+", "O+"])        # three types with enough history: two calls' worth
        with mock.patch.object(app, "business_today", return_value=TODAY), mock.patch.object(app, "_facilities_to_warm", return_value=[bank]), \
                contextlib.redirect_stdout(io.StringIO()):
            app._warm_all_forecasts()
        with engine.connect() as c:
            cached = {r[0]: (r[1], r[2]) for r in c.execute(text(
                "SELECT blood_type, count(*), max(trained_through_date) FROM facility_forecast_cache WHERE facility_id = :f GROUP BY 1"), {"f": bank})}
        self.assertEqual(sorted(cached), ["A+", "B+", "O+"])
        for count, trained in cached.values():
            self.assertEqual((count, trained), (len(app.FORECAST_CHECKPOINTS), TODAY))
        with mock.patch.object(app, "_fit_and_cache_sarimax", side_effect=AssertionError("a user load had to fit")):
            result = self.forecast(bank)                                # the dashboard's own load afterwards
        self.assertEqual(result["pending_types"], [])
        self.assertEqual(sorted(result["sarimax_orders"]), ["A+", "B+", "O+"])

    def test_each_call_respects_the_two_fit_cap(self):
        bank = self.make_bank(days=45)
        fit = FakeFit()
        sizes, real_get = [], app.get_forecast

        def spy(facility_id):
            before = len(fit.calls)
            out = real_get(facility_id=facility_id)
            sizes.append(len(fit.calls) - before)
            return out

        with mock.patch.object(app, "business_today", return_value=TODAY), mock.patch.object(app, "_fit_and_cache_sarimax", fit), \
                mock.patch.object(app, "get_forecast", spy), mock.patch.object(app.time, "sleep"):
            app._warm_one_facility(bank)
        self.assertTrue(sizes and all(n <= app.MAX_SARIMAX_FITS_PER_REQUEST for n in sizes), sizes)
        self.assertEqual(len(fit.calls), 8)                              # every type got its turn, two per call

    def test_it_does_not_fit_while_a_user_load_holds_the_facilitys_advisory_lock(self):
        bank = self.make_bank(days=45)
        fit = FakeFit()
        holder = engine.connect()
        try:
            self.assertTrue(holder.execute(text("SELECT pg_try_advisory_lock(:ns, :f)"), {"ns": app._FORECAST_LOCK_NAMESPACE, "f": bank}).scalar())
            with mock.patch.object(app, "business_today", return_value=TODAY), mock.patch.object(app, "_fit_and_cache_sarimax", fit):
                summary = app._warm_one_facility(bank)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:ns, :f)"), {"ns": app._FORECAST_LOCK_NAMESPACE, "f": bank})
            holder.close()
        self.assertEqual(fit.calls, [])                                  # it waited for no one and fitted nothing
        self.assertIn("another request is fitting", summary)


class SameNotificationsAsAUserLoadTests(Seeded):
    """Warming goes through get_forecast, so it writes the shortage notification a user load would have, once, with the same text."""

    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed_stock_so_the_projection_matters(self, bank):
        with engine.begin() as c:
            c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
                           "SELECT :tag || '-' || g, 'O+', 'Packed RBC', 'A', 450, :c, :e, :f FROM generate_series(1, 50) g"),
                      {"tag": uuid.uuid4().hex[:6], "c": TODAY - timedelta(days=2), "e": TODAY + timedelta(days=60), "f": bank})
        self.addCleanup(lambda: self.drop_units(bank))

    def drop_units(self, bank):
        with engine.begin() as c:
            c.execute(text("DELETE FROM blood_units WHERE facility_id = :f"), {"f": bank})

    def test_warming_emits_the_shortage_a_user_load_would_and_a_later_load_adds_nothing(self):
        warmed, loaded = self.make_bank(days=45), self.make_bank(days=45)
        for bank in (warmed, loaded):
            self.seed_stock_so_the_projection_matters(bank)
        fit = FakeFit(declining_type="O+")
        with mock.patch.object(app, "business_today", return_value=TODAY), mock.patch.object(app, "_fit_and_cache_sarimax", fit):
            with contextlib.redirect_stdout(io.StringIO()):
                app._warm_one_facility(warmed)                           # at night
            while self.forecast(loaded)["pending_types"]:                 # a user, later: the dashboard asks again until nothing is pending
                pass
            night = self.notifications(warmed)
            user = self.notifications(loaded)
            self.forecast(warmed)                                        # the user arrives after the warm-up: no second notification
        self.assertEqual(len(night), 1, night)
        self.assertTrue(night[0].startswith("O+: projected below its minimum"), night)
        self.assertEqual(night, user)                                    # the same words, from the same path
        self.assertEqual(self.notifications(warmed), night)
