"""A warm GET /forecast uses ONE connection: the snapshot write, the history, the cache and the alert state are read in a single
transaction, and the alert check opens a second connection only when it has something to write. A cold one still commits before any
fit, so the snapshot row locks are never held across a model fit. Needs the scratch database from conftest.py (TEST_DATABASE_URL)."""
import itertools
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import event, text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair

TYPES = sorted(app.VALID_BLOOD_TYPES)
LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue [grid-searched]"


class OneConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)
        with engine.begin() as c:
            for bt in TYPES:
                c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, :b, 10, 100)"),
                          {"f": self.bank, "b": bt})
            for n in range(40):  # 40 recorded days ending today: the SARIMAX branch
                for bt in TYPES:
                    c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) VALUES (:d, :b, 50, :f)"),
                              {"d": TODAY - timedelta(days=39 - n), "b": bt, "f": self.bank})
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            for t in ("facility_forecast_cache", "forecast_alert_state", "inventory_snapshots", "blood_type_thresholds"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.bank})

    def cache(self, blood_types=TYPES):
        with engine.begin() as c:
            for bt in blood_types:
                for cp in app.FORECAST_CHECKPOINTS:
                    c.execute(text("INSERT INTO facility_forecast_cache (facility_id, blood_type, forecast_date, forecast_units, lower_units, "
                                   "upper_units, trained_through_date, model_order) VALUES (:f, :b, :d, 50, 40, 60, :t, :m)"),
                              {"f": self.bank, "b": bt, "d": TODAY + timedelta(days=cp), "t": TODAY, "m": LABEL})

    def watched(self, fn):
        checkouts, statements = [], []
        on_checkout = lambda *a: checkouts.append(1)  # noqa: E731
        on_statement = lambda c, cur, statement, *a: statements.append(" ".join(statement.split()))  # noqa: E731
        event.listen(engine, "checkout", on_checkout)
        event.listen(engine, "before_cursor_execute", on_statement)
        try:
            result = fn()
        finally:
            event.remove(engine, "checkout", on_checkout)
            event.remove(engine, "before_cursor_execute", on_statement)
        return result, len(checkouts), statements

    def forecast(self):
        with mock.patch.object(app, "business_today", return_value=TODAY):
            return app.get_forecast(facility_id=self.bank)

    def test_a_warm_load_with_nothing_to_notify_is_one_connection_and_five_statements(self):
        self.cache()
        result, connections, statements = self.watched(self.forecast)
        self.assertEqual(result["pending_types"], [])
        self.assertEqual(sorted(result["sarimax_orders"]), TYPES)      # every type served from its cache: nothing was fitted
        self.assertEqual(connections, 1, statements)
        self.assertEqual(len(statements), 5, statements)               # facility+thresholds, snapshot write, history, cache, alert state
        self.assertTrue(statements[1].startswith("INSERT INTO inventory_snapshots"))  # the snapshot is still written, as before

    def test_the_response_is_what_it_was(self):
        self.cache()
        result = self.forecast()
        self.assertEqual(result["view"], "forecast")
        self.assertEqual(result["forecast_source"], "sarimax_facility_history")
        self.assertEqual(result["types_total"], 8)
        self.assertEqual(len(result["series"]), len(app.FORECAST_CHECKPOINTS))
        self.assertEqual(set(result["type_bounds"]), set(TYPES))

    def test_a_load_that_has_a_shortage_to_report_opens_one_more_connection_and_notifies_once(self):
        self.cache()
        with engine.begin() as c:   # O+ sits at 50 now but the cache says it falls to 5: below its minimum of 10 at the horizon
            c.execute(text("UPDATE facility_forecast_cache SET forecast_units = 5, lower_units = 2, upper_units = 9 "
                           "WHERE facility_id = :f AND blood_type = 'O+' AND forecast_date > :t"), {"f": self.bank, "t": TODAY})
            c.execute(text("UPDATE inventory_snapshots SET units = 50 WHERE facility_id = :f"), {"f": self.bank})
            c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
                           "SELECT :tag || '-' || g, 'O+', 'Packed RBC', 'A', 450, :c, :e, :f FROM generate_series(1, 50) g"),
                      {"tag": self.tag, "c": TODAY - timedelta(days=2), "e": TODAY + timedelta(days=30), "f": self.bank})
        self.addCleanup(self.drop_units)
        _, connections, statements = self.watched(self.forecast)
        self.assertEqual(connections, 2, statements)                   # the load, plus the alert write
        count = lambda: self.notifications()                            # noqa: E731
        self.assertEqual(count(), 1)
        _, connections, statements = self.watched(self.forecast)       # the next load finds the same shortage: nothing to write
        self.assertEqual(connections, 1, statements)
        self.assertEqual(count(), 1)

    def drop_units(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM notifications WHERE facility_id = :f"), {"f": self.bank})
            c.execute(text("DELETE FROM blood_units WHERE facility_id = :f"), {"f": self.bank})

    def notifications(self):
        with engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM notifications WHERE facility_id = :f AND type = 'forecast_shortage'"), {"f": self.bank}).scalar()

    def test_a_cold_load_commits_the_snapshot_before_any_model_is_fitted(self):
        self.cache([bt for bt in TYPES if bt != "A+"])                 # A+ has no cache for today: it must be fitted
        with engine.begin() as c:                                       # no row for today yet: the load itself must write it
            c.execute(text("DELETE FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d"), {"f": self.bank, "d": TODAY})
        seen = {}

        def fit(facility_id, blood_type, daily_series, today, observed_dates=None):
            with engine.connect() as other:                             # a different connection: it sees only what was committed
                seen["snapshot_rows"] = other.execute(text("SELECT count(*) FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d"),
                                                      {"f": facility_id, "d": TODAY}).scalar()
                seen["locked"] = other.execute(text("SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                                                    "WHERE a.state = 'idle in transaction' AND a.pid <> pg_backend_pid() AND l.locktype = 'transactionid'")).scalar()
            return None                                                 # "could not fit": served the linear trend

        with mock.patch.object(app, "_fit_and_cache_sarimax", side_effect=fit):
            result = self.forecast()
        self.assertEqual(seen["snapshot_rows"], 8)                      # today's eight rows were already committed when the fit began
        self.assertEqual(seen["locked"], 0)                             # no transaction was left open across the fit
        self.assertIn("A+", result["sarimax_fallback_types"])           # and the cold path still produced its answer


class ShortageCheckMirrorTests(unittest.TestCase):
    """_shortage_check_needs_writes must say "no" only when the real check writes nothing: run both over every combination."""

    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM notifications WHERE facility_id = :f"), {"f": self.bank})
            c.execute(text("DELETE FROM forecast_alert_state WHERE facility_id = :f"), {"f": self.bank})

    def subsets(self, items):
        return [set(c) for r in range(len(items) + 1) for c in itertools.combinations(items, r)]

    def test_it_agrees_with_the_real_check_for_every_combination(self):
        types = ["A+", "B+", "O+"]
        for tracked in self.subsets(types):
            for alerting in self.subsets(types):
                for evaluated in (None, set(types), {"A+"}, set()):
                    with engine.begin() as c:
                        c.execute(text("DELETE FROM forecast_alert_state WHERE facility_id = :f"), {"f": self.bank})
                        for bt in tracked:
                            c.execute(text("INSERT INTO forecast_alert_state (facility_id, blood_type, alerting) VALUES (:f, :b, true)"),
                                      {"f": self.bank, "b": bt})
                        tracked_now = app._read_tracked_alert_types(c, self.bank)
                    alerts = [{"type": bt, "days_until_threshold": 9} for bt in sorted(alerting)]
                    predicted = app._shortage_check_needs_writes(tracked_now, alerts, evaluated)
                    writes = []
                    record = lambda c, cur, statement, *a: writes.append(" ".join(statement.split()))  # noqa: E731
                    event.listen(engine, "before_cursor_execute", record)
                    try:
                        with engine.begin() as c:
                            app._check_forecast_shortage_notifications(c, self.bank, alerts, evaluated_types=evaluated, today=TODAY)
                    finally:
                        event.remove(engine, "before_cursor_execute", record)
                    wrote = any(w.startswith(("INSERT", "UPDATE")) for w in writes)
                    self.assertEqual(predicted, wrote, (sorted(tracked), sorted(alerting), evaluated, writes))
