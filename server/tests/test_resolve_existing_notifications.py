"""server/resolve_existing_notifications.py: the one-off that resolves the notifications written before the notification rebuild.

It resolves (marks read, never deletes) the retired unit_expiry rows and everything the sweep's rules resolve, with no date
limit, and leaves the events that happened (accepted, declined, cancelled, completed, units failed) and forecast_shortage alone,
so the badge still says so. It saves the ids first and records the exact SQL that undoes it.

What these tests prove: which rows it touches and which it leaves, that the ids are on disk before the first row changes, that
the undo restores the exact prior state, that a dry run and a refused run change nothing, and that a second run does nothing.
What they do not prove: anything about the production rows (that was measured separately, read-only).

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import json
import sys
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import text

import main as app
import resolve_existing_notifications as script
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_transitions import TransitionBase

OLD = "2026-09-25T10:00:00Z"  # rows as the old code left them, from before the notification rebuild


class ResolveExistingTests(TransitionBase):
    def setUp(self):
        super().setUp()
        self.dump_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [f.unlink() for f in self.dump_dir.glob("*")] or self.dump_dir.rmdir())

    def row(self, facility, notif_type, link=None, message="x", created=OLD, read=False):
        with engine.begin() as c:
            return c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at, read_at) "
                                  "VALUES (:f, :t, :m, :l, CAST(:c AS timestamptz), CASE WHEN :r THEN CAST(:c AS timestamptz) END) RETURNING id"),
                             {"f": facility, "t": notif_type, "m": message, "l": link, "c": created, "r": read}).scalar()

    def build(self):
        """One row of each kind, written 'before the rebuild'. Returns (should resolve, should stay unread, already read)."""
        pending = self.new_request()
        accepted = self.new_request()
        self.accept(accepted)
        released = self.new_request()
        self.accept(released)
        self.release(released)
        done = self.new_request()
        self.accept(done)
        self.release(done)
        app.confirm_receipt(done, facility_id=self.hospital, user_id=self.hospital_user)
        with engine.begin() as c:  # the transfer functions resolved their own rows; start from rows as the old code left them
            c.execute(text("DELETE FROM notifications WHERE facility_id IN (:b, :h)"), {"b": self.bank, "h": self.hospital})
        expired_offer = "X has 2 near-expiry O+ Packed RBC units that expire in 1 day, 3 kilometers away"
        live_offer = "X has 2 near-expiry O+ Packed RBC units that expire in 400 days, 3 kilometers away"
        resolve = {
            "unit_expiry": self.row(self.bank, "unit_expiry", "inventory"),
            "orphan": self.row(self.bank, "incoming_request", "requests:2000000000"),
            "incoming, accepted": self.row(self.bank, "incoming_request", f"requests:{accepted}"),
            "legacy prompt, supplier, released": self.row(self.bank, "transfer_confirmation_needed", f"requests:{released}"),
            "legacy prompt, requester, received": self.row(self.hospital, "transfer_confirmation_needed", f"requests:{done}"),
            "receipt prompt, received": self.row(self.hospital, "receipt_needed", f"requests:{done}"),
            "offer, units expired": self.row(self.hospital, "expiring_unit_alert", "sourcing:O+", message=expired_offer),
            "event on a request that is gone (nothing left to open)": self.row(self.bank, "request_cancelled", "requests:2000000001"),
        }
        stay = {
            "incoming, still pending": self.row(self.bank, "incoming_request", f"requests:{pending}"),
            "receipt prompt, not yet received": self.row(self.hospital, "receipt_needed", f"requests:{released}"),
            "legacy prompt, requester, not yet received": self.row(self.hospital, "transfer_confirmation_needed", f"requests:{released}"),
            "offer, units not expired": self.row(self.hospital, "expiring_unit_alert", "sourcing:O+", message=live_offer),
            "event: accepted": self.row(self.hospital, "request_accepted", f"requests:{accepted}"),
            "event: declined": self.row(self.hospital, "request_declined", f"requests:{done}"),
            "event: cancelled": self.row(self.bank, "request_cancelled", f"requests:{done}"),
            "event: completed": self.row(self.bank, "transfer_completed", f"requests:{done}"),
            "event: units failed": self.row(self.bank, "transfer_units_failed", f"requests:{done}"),
            "forecast_shortage": self.row(self.bank, "forecast_shortage", "dashboard"),
            "blast_complete": self.row(self.bank, "blast_complete", "chat"),
        }
        already = self.row(self.bank, "incoming_request", "requests:2000000002", read=True)
        return resolve, stay, already

    def unread(self, ids):
        return {i for (i,) in q("SELECT id FROM notifications WHERE id = ANY(:i) AND read_at IS NULL", i=list(ids))}

    def run_it(self, **kw):
        return script.run(self.dump_dir, **kw)

    # ---- what it touches --------------------------------------------------
    def test_it_resolves_what_the_rules_resolve_and_leaves_every_event_unread(self):
        resolve, stay, _ = self.build()
        result = self.run_it()
        self.assertEqual(self.unread(resolve.values()), set(), {k for k, v in resolve.items() if v in self.unread(resolve.values())})
        self.assertEqual(self.unread(stay.values()), set(stay.values()), {k for k, v in stay.items() if v not in self.unread(stay.values())})
        by_type = {t: (before, resolved, left) for t, before, resolved, left in result["by_type"]}
        self.assertEqual(by_type["unit_expiry"][1], by_type["unit_expiry"][0], "every unread unit_expiry row")
        for kept in ("request_accepted", "request_declined", "transfer_completed", "transfer_units_failed", "forecast_shortage", "blast_complete"):
            self.assertEqual(by_type[kept][1], 0, f"{kept} is left unread")
            self.assertGreaterEqual(by_type[kept][2], 1)
        self.assertEqual(by_type["request_cancelled"][1], 1, "only the cancelled event whose request is gone")
        self.assertGreaterEqual(by_type["request_cancelled"][2], 1, "and the one whose request exists stays")

    def test_nothing_is_deleted_and_an_already_read_row_keeps_its_time(self):
        resolve, stay, already = self.build()
        before = q("SELECT count(*) FROM notifications")[0][0]
        read_before = q("SELECT read_at FROM notifications WHERE id = :i", i=already)[0][0]
        self.run_it()
        self.assertEqual(q("SELECT count(*) FROM notifications")[0][0], before)
        self.assertEqual(q("SELECT read_at FROM notifications WHERE id = :i", i=already)[0][0], read_before)

    def test_a_second_run_resolves_nothing(self):
        self.build()
        self.run_it()
        again = self.run_it()
        self.assertEqual(again["resolved"], 0)

    # ---- the ids first, and the exact undo -------------------------------
    def test_the_ids_are_saved_before_the_first_row_changes(self):
        resolve, stay, _ = self.build()

        def refuse(*a, **k):
            raise RuntimeError("stop after the dump, before any change")

        with mock.patch.object(app, "_resolve_stale_notifications", refuse):
            with self.assertRaises(RuntimeError):
                self.run_it()
        (dump_file,) = self.dump_dir.glob("notification_cleanup_*.json")
        saved = {r["id"] for r in json.loads(dump_file.read_text(encoding="utf-8"))["unread_before"]}
        self.assertTrue(set(resolve.values()) | set(stay.values()) <= saved, "every unread id was on disk")
        self.assertEqual(self.unread(resolve.values()), set(resolve.values()), "and nothing had changed")

    def test_the_recorded_sql_undoes_the_run_exactly(self):
        resolve, stay, already = self.build()
        everything = lambda: q("SELECT id, read_at FROM notifications ORDER BY id")
        state_before = everything()
        result = self.run_it()
        self.assertNotEqual(everything(), state_before)
        (dump_file,) = self.dump_dir.glob("notification_cleanup_*.json")
        dump = json.loads(dump_file.read_text(encoding="utf-8"))
        self.assertEqual(result["dump"], str(dump_file))
        self.assertEqual(dump["result"]["resolved"], result["resolved"])
        with engine.begin() as c:
            c.execute(text(dump["undo_sql"]))
        self.assertEqual(everything(), state_before)

    # ---- changing nothing ------------------------------------------------
    def test_a_dry_run_changes_nothing_and_writes_no_file(self):
        resolve, stay, _ = self.build()
        state_before = q("SELECT id, read_at FROM notifications ORDER BY id")
        result = self.run_it(dry_run=True)
        self.assertGreaterEqual(result["resolved"], len(resolve))
        self.assertEqual(q("SELECT id, read_at FROM notifications ORDER BY id"), state_before)
        self.assertEqual(list(self.dump_dir.glob("*")), [])

    def test_it_stops_and_rolls_back_if_it_would_resolve_an_event(self):
        resolve, stay, _ = self.build()
        event = stay["event: accepted"]
        real = app._resolve_stale_notifications

        def too_much(conn, facility_id, since, today=None):
            n = real(conn, facility_id, since, today)
            conn.execute(text("UPDATE notifications SET read_at = now() WHERE id = :i"), {"i": event})
            return n

        with mock.patch.object(app, "_resolve_stale_notifications", too_much):
            with self.assertRaisesRegex(RuntimeError, "refusing to commit"):
                self.run_it()
        self.assertEqual(self.unread(resolve.values()), set(resolve.values()), "the whole run was rolled back")
        self.assertEqual(self.unread([event]), {event})

    def test_it_refuses_without_the_production_flag_and_touches_nothing(self):
        resolve, stay, _ = self.build()
        with mock.patch.object(sys, "argv", ["resolve_existing_notifications.py"]):
            with self.assertRaises(SystemExit) as cm:
                script.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.unread(resolve.values()), set(resolve.values()))


if __name__ == "__main__":
    unittest.main()
