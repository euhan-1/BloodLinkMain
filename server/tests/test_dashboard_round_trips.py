"""The dashboard's endpoints send few statements: each statement is a network round trip to the database (about 130 ms from the
deployed API to Supabase), so the count is what the page waits for. These tests pin the batching without changing any answer:
the batched reads must return exactly what the one-at-a-time reads returned.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import unittest
import uuid
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event, text

import main as app
from database import engine, read_engine
from tests.test_expired_transfer import TODAY, make_pair
from tests.test_request_component import add

LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue [grid-searched]"


@contextmanager
def statements():
    """Every statement the engine sends inside the block (the pool's pre-ping, BEGIN and COMMIT are not statements here)."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    for e in (engine, read_engine):  # a read endpoint now uses the read engine, a writing one the normal engine
        event.listen(e, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        for e in (engine, read_engine):
            event.remove(e, "before_cursor_execute", record)


class SummaryRoundTripsTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        with engine.begin() as c:
            for f in (self.bank, self.hospital):
                for bt in sorted(app.VALID_BLOOD_TYPES):
                    c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) "
                                   "VALUES (:f, :b, 1, 9)"), {"f": f, "b": bt})
        add(self.bank, f"{self.tag}-1", 20)
        add(self.bank, f"{self.tag}-2", 20)
        add(self.bank, f"{self.tag}-3", 1)                            # inside the packed RBC margin
        add(self.bank, f"{self.tag}-P", 20, component="Platelets")
        add(self.bank, f"{self.tag}-A", 20, blood_type="A+")

    def test_a_banks_summary_is_two_statements_not_four(self):
        with statements() as seen:
            rows = app.get_inventory_summary(facility_id=self.bank)
        self.assertEqual(len(seen), 2, seen)
        o_pos = next(r for r in rows if r["blood_type"] == "O+")
        self.assertEqual(o_pos["units"], 4)
        self.assertEqual(o_pos["can_give"], {"Packed RBC": 1, "Platelets": 0})  # 2 releasable RBC - minimum 1; 1 platelet - 1
        self.assertNotIn("facility_type", o_pos)                                # the join's extra column is not in the response

    def test_a_hospitals_summary_is_one_statement_and_has_no_can_give(self):
        with statements() as seen:
            rows = app.get_inventory_summary(facility_id=self.hospital)
        self.assertEqual(len(seen), 1, seen)
        self.assertTrue(all(r["can_give"] == {} for r in rows))

    def test_the_batched_counts_equal_the_per_component_counts(self):
        with engine.connect() as c:
            one_by_one = {comp: app._releasable_counts(c, comp, TODAY, self.bank) for comp in app.COMPONENTS}
            together = app._releasable_counts_multi(c, list(app.COMPONENTS), TODAY, self.bank)
            everyone = app._releasable_counts_multi(c, list(app.COMPONENTS), TODAY)
            everyone_one_by_one = {comp: app._releasable_counts(c, comp, TODAY) for comp in app.COMPONENTS}
        self.assertEqual(together, one_by_one)
        self.assertEqual(everyone, everyone_one_by_one)

    def test_the_batch_uses_the_one_definition_of_a_releasable_unit(self):
        import inspect
        self.assertIn("RELEASABLE_UNITS_SQL", inspect.getsource(app._releasable_counts_multi))


class ForecastCacheReadTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)
        self.series = {bt: [(TODAY - timedelta(days=1), 50), (TODAY, 60 + i)] for i, bt in enumerate(sorted(app.VALID_BLOOD_TYPES))}

    def store(self, blood_type, trained=None, skip_checkpoint=None, label=LABEL):
        with engine.begin() as c:
            for cp in app.FORECAST_CHECKPOINTS:
                if cp == skip_checkpoint:
                    continue
                c.execute(text(
                    "INSERT INTO facility_forecast_cache (facility_id, blood_type, forecast_date, forecast_units, lower_units, "
                    "upper_units, trained_through_date, model_order) VALUES (:f, :b, :d, :u, :l, :h, :t, :m)"),
                    {"f": self.bank, "b": blood_type, "d": TODAY + timedelta(days=cp), "u": 100 - cp, "l": 90 - cp, "h": 110 - cp,
                     "t": trained or TODAY, "m": label})

    def test_every_type_is_one_read_and_the_same_answers(self):
        for bt in ("A+", "O+", "B-"):
            self.store(bt)
        self.store("AB+", trained=TODAY - timedelta(days=1))       # stale: trained yesterday
        self.store("O-", skip_checkpoint=15)                       # incomplete: a checkpoint is missing
        with engine.connect() as c:
            with statements() as seen:
                many = app._read_cached_sarimax_many(c, self.bank, self.series, TODAY)
            one_by_one = {bt: app._read_cached_sarimax(c, self.bank, bt, s, TODAY) for bt, s in self.series.items()}
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(many, one_by_one)
        self.assertIsNotNone(many["O+"])
        self.assertEqual(many["O+"]["checkpoints"][0]["units"], 60 + sorted(app.VALID_BLOOD_TYPES).index("O+"))  # day 0 is live
        self.assertEqual(many["O+"]["model_order"], "SARIMAX(0,1,4)x(1,0,1,7)+dengue [grid-searched]")
        for bt in ("AB+", "O-", "B+"):                              # stale, incomplete and absent are all "not cached"
            self.assertIsNone(many[bt], bt)

    def test_collect_reads_the_cache_once_when_everything_is_cached(self):
        long_series = {bt: [(TODAY - timedelta(days=n), 40 + n % 7) for n in range(40, -1, -1)] for bt in sorted(app.VALID_BLOOD_TYPES)}
        for bt in long_series:
            self.store(bt)
        with statements() as seen:
            results, pending = app._collect_sarimax_results(self.bank, long_series, TODAY, set(d for s in long_series.values() for d, _ in s))
        self.assertEqual(pending, [])
        self.assertTrue(all(r is not None for r in results.values()))
        self.assertEqual(len(seen), 1, seen)                       # was one statement per type: eight


class ShortageNotificationRoundTripsTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, _ = make_pair(self, self.tag)

    def notifications(self):
        with engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM notifications WHERE facility_id = :f AND type = 'forecast_shortage'"),
                             {"f": self.bank}).scalar()

    def check(self, alerts):
        with engine.begin() as c:
            app._check_forecast_shortage_notifications(c, self.bank, alerts, today=TODAY)

    def test_a_type_already_alerting_costs_no_write_and_no_second_notification(self):
        alert = [{"type": "O+", "days_until_threshold": 9}]
        self.check(alert)
        self.assertEqual(self.notifications(), 1)
        with statements() as seen:
            self.check(alert)                                       # the next dashboard load finds the same shortage
        self.assertEqual(self.notifications(), 1)
        self.assertEqual([s for s in seen if "INSERT" in s or "UPDATE" in s], [], seen)
        self.assertEqual(len(seen), 1, seen)                        # one read of the tracked set, nothing else

    def test_a_new_type_still_flips_once(self):
        self.check([{"type": "O+", "days_until_threshold": 9}])
        self.check([{"type": "O+", "days_until_threshold": 9}, {"type": "A+", "days_until_threshold": 12}])
        self.assertEqual(self.notifications(), 2)

    def test_a_type_that_stops_alerting_is_cleared_and_can_alert_again(self):
        self.check([{"type": "O+", "days_until_threshold": 9}])
        self.check([])
        with engine.connect() as c:
            alerting = c.execute(text("SELECT alerting FROM forecast_alert_state WHERE facility_id = :f AND blood_type = 'O+'"),
                                 {"f": self.bank}).scalar()
        self.assertFalse(alerting)
        self.check([{"type": "O+", "days_until_threshold": 9}])
        self.assertEqual(self.notifications(), 2)
