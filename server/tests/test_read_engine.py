"""The read engine (database.py): endpoints that only read run on a separate autocommit engine with no pre-ping, so a read costs the one
round trip of its own statement instead of four. These tests prove, on the scratch database:
  - the read engine's connections are autocommit and read-only AT THE DATABASE (so an accidental write fails loudly instead of
    committing by itself), and every read endpoint is free of writes, by reading its code and by running it there;
  - each read endpoint sends the statements it should and leaves no transaction open;
  - GET /notifications/unread-count writes nothing and still returns exactly the number the sweep would leave unread;
  - a dead connection (killed on the server while pooled, as after a Render sleep or a pooler restart) is replaced and the request
    still succeeds, without a user ever seeing the error.

Needs the scratch database from conftest.py (TEST_DATABASE_URL)."""
import ast
import inspect
import re
import textwrap
import unittest
import uuid
from unittest import mock

import psycopg2.extensions
from sqlalchemy import event, exc, text

import auth
import database
import main as app
from database import engine, read_engine
from tests.test_expired_transfer import TODAY, make_pair
from tests.test_notifications import WRITTEN, FlowBase
from tests.test_request_component import add

READ_ONLY_FUNCTIONS = [
    app.health, app._check_database, app._lookup_account, app.get_me, app.get_inventory, app.get_inventory_summary,
    app._releasable_counts_multi, app.list_facilities, app.get_expiring_inventory, app.list_requests, app.list_incoming_requests,
    app.notifications_unread_count,
]
WRITE_WORDS = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|LOCK|COMMIT|FOR UPDATE)\b")
NORMAL_ENGINE = re.compile(r"(?<![A-Za-z_])engine\.(begin|connect)\(")  # `read_engine.connect(` does not match: it has a letter before


def code_without_docstring(fn):
    fn = inspect.unwrap(fn)
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
        node.body = node.body[1:]
    return ast.unparse(node)


class ReadOnlyBySourceTests(unittest.TestCase):
    def test_no_read_endpoint_has_a_write_statement_or_uses_the_normal_engine(self):
        for fn in READ_ONLY_FUNCTIONS:
            code = code_without_docstring(fn)
            self.assertIsNone(WRITE_WORDS.search(code), f"{fn.__name__}: {WRITE_WORDS.search(code)}")
            self.assertIsNone(NORMAL_ENGINE.search(code), f"{fn.__name__} uses the normal engine")

    def test_the_stale_condition_the_unread_count_shares_with_the_sweep_has_no_write_in_it(self):
        self.assertIsNone(WRITE_WORDS.search(app._STALE_NOTIFICATION_SQL))

    def test_every_one_of_them_is_marked_retry_read_or_is_called_only_from_one_that_is(self):
        for fn in (app.get_me, app.get_inventory, app.get_inventory_summary, app.list_facilities, app.get_expiring_inventory,
                   app.list_requests, app.list_incoming_requests, app.notifications_unread_count, app._check_database, app._lookup_account):
            self.assertTrue(hasattr(fn, "__wrapped__"), fn.__name__)  # functools.wraps in retry_read

    def test_the_engines_are_configured_as_documented(self):
        self.assertTrue(engine.pool._pre_ping, "the engine that writes still pings every connection it hands out")
        self.assertFalse(read_engine.pool._pre_ping)
        self.assertLessEqual(read_engine.pool.size() + read_engine.pool._max_overflow, 10)


class ReadEngineConnectionTests(unittest.TestCase):
    def test_connections_are_autocommit_and_read_only_at_the_database(self):
        with read_engine.connect() as c:
            self.assertEqual(c.execute(text("SHOW default_transaction_read_only")).scalar(), "on")
            raw = c.connection.dbapi_connection
            self.assertTrue(raw.autocommit)
            self.assertEqual(raw.info.transaction_status, psycopg2.extensions.TRANSACTION_STATUS_IDLE)  # no transaction left open
            with self.assertRaises(exc.DBAPIError) as ctx:
                c.execute(text("UPDATE notifications SET read_at = now() WHERE false"))   # even one that would touch no rows
            self.assertIn("read-only", str(ctx.exception).lower())

    def test_the_normal_engine_is_unchanged_and_still_writes(self):
        with engine.begin() as c:
            self.assertEqual(c.execute(text("SHOW default_transaction_read_only")).scalar(), "off")
            c.execute(text("UPDATE notifications SET read_at = now() WHERE false"))

    def test_the_connection_names_itself_so_it_can_be_told_apart_in_pg_stat_activity(self):
        with read_engine.connect() as c:
            self.assertEqual(c.execute(text("SHOW application_name")).scalar(), "bloodlink-read")


class StatementsTests(unittest.TestCase):
    def setUp(self):
        app._active_account_until.clear()
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        with engine.begin() as c:
            for f in (self.bank, self.hospital):
                for bt in sorted(app.VALID_BLOOD_TYPES):
                    c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, :b, 1, 9)"),
                              {"f": f, "b": bt})
            self.uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                 {"e": f"re-{self.tag}@example.com", "f": self.bank}).scalar()
        add(self.bank, f"{self.tag}-1", 3)
        add(self.bank, f"{self.tag}-2", 30, component="Platelets")

    def counted(self, fn):
        seen = []
        record = lambda c, cur, statement, *a: seen.append(" ".join(statement.split()))  # noqa: E731
        for e in (engine, read_engine):
            event.listen(e, "before_cursor_execute", record)
        try:
            result = fn()
        finally:
            for e in (engine, read_engine):
                event.remove(e, "before_cursor_execute", record)
        return result, seen

    def test_each_read_is_the_statements_it_needs_and_leaves_no_transaction_open(self):
        cases = [
            ("health", lambda: app.health(), 1),
            ("auth/me", lambda: app.get_me(user={"sub": str(self.uid)}), 1),
            ("active check", lambda: app._lookup_account(self.uid), 1),
            ("summary (bank)", lambda: app.get_inventory_summary(facility_id=self.bank), 2),
            ("summary (hospital)", lambda: app.get_inventory_summary(facility_id=self.hospital), 1),
            ("expiring", lambda: app.get_expiring_inventory(facility_id=self.bank), 1),
            ("incoming", lambda: app.list_incoming_requests(facility_id=self.bank), 1),
            ("outgoing", lambda: app.list_requests(facility_id=self.hospital), 1),
            ("inventory", lambda: app.get_inventory(facility_id=self.bank), 1),
            ("unread count", lambda: app.notifications_unread_count(facility_id=self.bank), 1),
            ("facilities", lambda: app.list_facilities(user={"sub": str(self.uid)}), 1),
        ]
        for label, fn, expected in cases:
            _, seen = self.counted(fn)
            self.assertEqual(len(seen), expected, (label, seen))
            self.assertFalse([s for s in seen if re.match(r"(BEGIN|COMMIT|ROLLBACK)", s)], (label, seen))
        with read_engine.connect() as c:  # whatever ran last left the pooled connection idle, not inside a transaction
            self.assertEqual(c.connection.dbapi_connection.info.transaction_status, psycopg2.extensions.TRANSACTION_STATUS_IDLE)

    def test_the_answers_are_the_same_as_before_they_moved(self):
        summary = {r["blood_type"]: r for r in app.get_inventory_summary(facility_id=self.bank)}
        self.assertEqual(summary["O+"]["units"], 2)
        self.assertEqual(summary["O+"]["can_give"], {"Packed RBC": 0, "Platelets": 0})  # 1 releasable RBC - minimum 1; the other 29 d platelet - 1
        self.assertEqual(app.get_me(user={"sub": str(self.uid)})["facility_id"], self.bank)
        self.assertEqual(app.health(), {"status": "ok"})
        self.assertEqual(app.get_expiring_inventory(facility_id=self.bank)["total_units"], 1)  # the 3-day unit
        self.assertIn(self.bank, [r["id"] for r in app.list_facilities(user={"sub": str(self.uid)})])
        self.assertEqual([u["din"] for u in app.get_inventory(facility_id=self.bank)], [f"{self.tag}-1", f"{self.tag}-2"])


class UnreadCountParityTests(FlowBase):
    def row(self, facility, notif_type, link, created_at=None, message="x"):
        with engine.begin() as c:
            return c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at) "
                                  "VALUES (:f, :t, :m, :l, COALESCE(:c, now())) RETURNING id"),
                             {"f": facility, "t": notif_type, "l": link, "c": created_at, "m": message}).scalar()

    def unread_rows(self, facility):
        with engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM notifications WHERE facility_id = :f AND read_at IS NULL"), {"f": facility}).scalar()

    def test_it_returns_what_the_sweep_would_leave_unread_and_writes_nothing(self):
        self.new_request()   # still pending: its prompt must stay counted
        accepted = self.new_request()
        self.accept(accepted)
        released = self.new_request()
        self.accept(released)
        self.release(released)
        done = self.new_request()
        self.accept(done)
        self.release(done)
        import main
        main.confirm_receipt(done, facility_id=self.hospital, user_id=self.hospital_user)
        # as if the events had not resolved anything (a missed event, a row restored from a dump), plus rows of every other kind
        with engine.begin() as c:
            c.execute(text("UPDATE notifications SET read_at = NULL, created_at = :n WHERE facility_id IN (:b, :h)"),
                      {"n": WRITTEN, "b": self.bank, "h": self.hospital})
        self.row(self.bank, "incoming_request", "requests:2000000000", created_at=WRITTEN)                 # its request no longer exists
        self.row(self.bank, "forecast_shortage", "dashboard")                                                 # not about a request
        self.row(self.hospital, "expiring_unit_alert", "sourcing:O+", created_at=WRITTEN,
                 message="X has 2 near-expiry O+ Packed RBC units that expire in 1 day, 2 kilometers away")  # long since expired
        before = {f: self.unread_rows(f) for f in (self.bank, self.hospital)}
        for facility in (self.bank, self.hospital):
            counted = main.notifications_unread_count(facility_id=facility)["unread"]
            self.assertEqual(self.unread_rows(facility), before[facility], "the count wrote something")
            self.assertLess(counted, before[facility], "the stale rows were not left out of the count")
            main.list_notifications(facility_id=facility)                  # the sweep (the list still runs it)
            self.assertEqual(counted, self.unread_rows(facility), "the count differs from what the sweep leaves unread")
            self.assertEqual(main.notifications_unread_count(facility_id=facility)["unread"], counted)  # and it is stable afterwards

    def test_latest_id_is_still_the_newest_row_read_or_not(self):
        a = self.row(self.bank, "forecast_shortage", "dashboard")
        b = self.row(self.bank, "incoming_request", "requests:2000000001", created_at=WRITTEN)   # stale: not counted, but still the newest
        got = app.notifications_unread_count(facility_id=self.bank)
        self.assertEqual(got["latest_id"], b)
        self.assertNotEqual(a, b)


class DeadConnectionTests(unittest.TestCase):
    def kill_every_read_connection(self):
        with engine.begin() as c:
            return c.execute(text("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                                  "WHERE application_name = 'bloodlink-read' AND pid <> pg_backend_pid()")).scalar()

    def fill_the_pool(self, n=4):
        conns = [read_engine.connect() for _ in range(n)]
        for c in conns:
            c.execute(text("SELECT 1"))
        for c in conns:
            c.close()

    def test_a_request_whose_pooled_connections_were_all_killed_still_succeeds_without_a_visible_error(self):
        self.fill_the_pool(4)
        killed = self.kill_every_read_connection()
        self.assertGreaterEqual(killed, 4)               # the pool really was full of connections the server has now closed
        with mock.patch.object(database.read_engine, "dispose", wraps=database.read_engine.dispose) as dispose:
            self.assertEqual(app.health(), {"status": "ok"})   # the first connection tried is dead; the answer is still ok
        dispose.assert_called_once()                       # and that is because the retry ran, discarding the pool
        for _ in range(6):                                 # none of the other pooled connections is left to fail on the next requests
            self.assertEqual(app.health(), {"status": "ok"})

    def test_every_read_endpoint_survives_it(self):
        tag = uuid.uuid4().hex[:6].upper()
        with engine.begin() as c:
            fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) VALUES (:n, 'bloodbank', true, true) RETURNING id"),
                            {"n": f"Dead {tag}"}).scalar()
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"dead-{tag}@example.com", "f": fid}).scalar()
        self.addCleanup(self.drop, fid)
        calls = [lambda: app.get_me(user={"sub": str(uid)}), lambda: app.get_inventory(facility_id=fid),
                 lambda: app.get_inventory_summary(facility_id=fid), lambda: app.get_expiring_inventory(facility_id=fid),
                 lambda: app.list_requests(facility_id=fid), lambda: app.list_incoming_requests(facility_id=fid),
                 lambda: app.notifications_unread_count(facility_id=fid), lambda: app.list_facilities(user={"sub": str(uid)}),
                 lambda: app._lookup_account(uid)]
        for call in calls:
            self.fill_the_pool(3)
            self.kill_every_read_connection()
            call()   # raises if the dead connection reached the caller

    def drop(self, fid):
        app._active_account_until.clear()
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE facility_id = :f"), {"f": fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": fid})

    def test_a_second_failure_is_raised_not_hidden(self):
        calls = {"n": 0}

        @database.retry_read
        def always_dead():
            calls["n"] += 1
            raise exc.OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"), connection_invalidated=True)

        with self.assertRaises(exc.OperationalError):
            always_dead()
        self.assertEqual(calls["n"], 2)  # tried once more, once

    def test_an_error_that_is_not_about_the_connection_is_raised_at_once(self):
        calls = {"n": 0}

        @database.retry_read
        def bad_sql():
            calls["n"] += 1
            raise exc.ProgrammingError("SELECT nope", {}, Exception("relation does not exist"))

        with self.assertRaises(exc.ProgrammingError):
            bad_sql()
        self.assertEqual(calls["n"], 1)

    def test_a_closed_connection_in_words_sqlalchemy_does_not_know_is_still_retried_once(self):
        calls = {"n": 0}

        @database.retry_read
        def odd_wording():
            calls["n"] += 1
            if calls["n"] == 1:
                raise exc.OperationalError("SELECT 1", {}, Exception("some pooler's own message about a closed server connection"))
            return "ok"

        self.assertEqual(odd_wording(), "ok")
        self.assertEqual(calls["n"], 2)

    def test_a_statement_timeout_is_not_retried(self):
        class Cancelled(Exception):
            pgcode = "57014"

        calls = {"n": 0}

        @database.retry_read
        def slow():
            calls["n"] += 1
            raise exc.OperationalError("SELECT pg_sleep(99)", {}, Cancelled("canceling statement due to statement timeout"))

        with self.assertRaises(exc.OperationalError):
            slow()
        self.assertEqual(calls["n"], 1)

    def test_the_wrapper_keeps_the_signature_fastapi_reads(self):
        sig = inspect.signature(app.get_me)
        self.assertEqual(list(sig.parameters), ["user"])
        self.assertIsNotNone(sig.parameters["user"].default)  # the Depends(...) default survived
