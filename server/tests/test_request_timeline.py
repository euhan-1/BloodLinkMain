"""The request timeline, and the request detail read that serves it and the receipt.

The events are DERIVED from the request's status and timestamps, never stored, so they cannot drift from the row. An event
is listed whenever the state shows it happened, even when its time or account was never captured; those read as None
("not recorded") rather than disappearing.

What these tests prove: the derivation for every lifecycle state (as a pure function and through the real read after real
transitions), that a historical row with NULL stamps still lists its events with gaps shown as None, that the released and
received events name the units and the outcome from the ledger (and fall back to request_unit_failures without one), that
only the two facilities can read a request, and the shape of the response.

What they do not prove: how the screen renders any of it, or anything about production.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import add
from tests.test_request_transitions import TransitionBase

T = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
NAMES = {1: "Requester", 2: "Supplier"}
EMAILS = {42: "supplier@example.test", 43: "requester@example.test"}


def req(**over):
    base = {"id": 7, "status": "pending", "quantity": 3, "requesting_facility_id": 1, "supplying_facility_id": 2, "created_at": T,
            "accepted_at": None, "accepted_by": None, "declined_at": None, "declined_by": None, "cancelled_at": None,
            "cancelled_by": None, "supplier_confirmed_at": None, "released_by": None, "requester_confirmed_at": None,
            "received_by": None}
    return {**base, **over}


def kinds(events):
    return [e["kind"] for e in events]


def ledger_row(din, outcome="received", reason=None, component="Packed RBC"):
    return {"din": din, "component": component, "outcome": outcome, "reason": reason}


class TimelineDerivationTests(unittest.TestCase):
    def timeline(self, units=(), failures=(), **over):
        return app._request_timeline(req(**over), NAMES, EMAILS, list(units), list(failures))

    def test_a_pending_request_has_only_its_sending(self):
        events = self.timeline()
        self.assertEqual(kinds(events), ["sent"])
        self.assertEqual((events[0]["at"], events[0]["facility_name"], events[0]["account_email"]), (T, "Requester", None))

    def test_each_state_lists_exactly_its_events_in_lifecycle_order(self):
        h = lambda n: T + timedelta(hours=n)
        cases = {
            "accepted": (dict(status="accepted", accepted_at=h(1), accepted_by=5), ["sent", "accepted"]),
            "released": (dict(status="accepted", accepted_at=h(1), supplier_confirmed_at=h(2), released_by=5),
                         ["sent", "accepted", "released"]),
            "completed": (dict(status="completed", accepted_at=h(1), supplier_confirmed_at=h(2), requester_confirmed_at=h(3)),
                          ["sent", "accepted", "released", "received"]),
            "failed": (dict(status="failed", accepted_at=h(1), supplier_confirmed_at=h(2), requester_confirmed_at=h(3)),
                       ["sent", "accepted", "released", "received"]),
            "declined": (dict(status="declined", declined_at=h(1), declined_by=5), ["sent", "declined"]),
            "cancelled": (dict(status="cancelled", cancelled_at=h(1), cancelled_by=6), ["sent", "cancelled"]),
        }
        for name, (over, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(kinds(self.timeline(**over)), expected)

    def test_each_event_is_on_the_right_facilitys_side(self):
        events = self.timeline(status="completed", accepted_at=T, supplier_confirmed_at=T, requester_confirmed_at=T)
        self.assertEqual({e["kind"]: e["facility_name"] for e in events},
                         {"sent": "Requester", "accepted": "Supplier", "released": "Supplier", "received": "Requester"})
        declined = self.timeline(status="declined", declined_at=T)
        cancelled = self.timeline(status="cancelled", cancelled_at=T)
        self.assertEqual((declined[-1]["facility_name"], cancelled[-1]["facility_name"]), ("Supplier", "Requester"))

    def test_the_accounts_email_is_carried_and_none_means_not_recorded(self):
        events = self.timeline(status="accepted", accepted_at=T, accepted_by=42)
        self.assertEqual([e["account_email"] for e in events], [None, "supplier@example.test"])  # nothing records who sent a request
        self.assertNotIn("user_id", events[1])  # an internal id tells a reader nothing

    def test_an_actor_id_that_no_longer_resolves_reads_as_not_recorded(self):
        events = self.timeline(status="accepted", accepted_at=T, accepted_by=999)
        self.assertIsNone(events[1]["account_email"])

    def test_a_historical_completed_request_lists_every_event_with_gaps_not_hidden(self):
        # Status and the two original timestamps are all such a row has.
        done = T + timedelta(hours=3)
        events = self.timeline(status="completed", supplier_confirmed_at=done - timedelta(hours=1), requester_confirmed_at=done)
        self.assertEqual(kinds(events), ["sent", "accepted", "released", "received"])
        accepted = events[1]
        self.assertIsNone(accepted["at"])
        self.assertIsNone(accepted["account_email"])
        self.assertEqual(accepted["order_at"], T)  # no time of its own: it sits where the event before it does
        self.assertEqual([e["order_at"] for e in events], sorted(e["order_at"] for e in events))

    def test_a_historical_declined_or_cancelled_request_still_shows_it_without_a_time(self):
        for status, kind in (("declined", "declined"), ("cancelled", "cancelled")):
            with self.subTest(status):
                events = self.timeline(status=status)
                self.assertEqual(kinds(events), ["sent", kind])
                self.assertIsNone(events[1]["at"])

    def test_order_at_never_goes_backwards(self):
        events = self.timeline(status="completed", accepted_at=T + timedelta(hours=1), supplier_confirmed_at=T + timedelta(hours=2),
                               requester_confirmed_at=T + timedelta(hours=3))
        self.assertEqual([e["order_at"] for e in events], [T, T + timedelta(hours=1), T + timedelta(hours=2), T + timedelta(hours=3)])

    def test_release_names_the_units_from_the_ledger(self):
        units = [ledger_row("D-2"), ledger_row("D-1")]
        released = self.timeline(units=units, status="accepted", accepted_at=T, supplier_confirmed_at=T)[-1]
        self.assertEqual(released["units"], {"count": 2, "dins": ["D-2", "D-1"], "components": ["Packed RBC"]})

    def test_release_without_a_ledger_says_units_are_not_recorded(self):
        released = self.timeline(status="accepted", accepted_at=T, supplier_confirmed_at=T)[-1]
        self.assertIsNone(released["units"])

    def test_receipt_counts_arrived_and_failed_with_the_reason_from_the_ledger(self):
        units = [ledger_row("A"), ledger_row("B", "failed", "expired_before_receipt"), ledger_row("C")]
        received = self.timeline(units=units, status="completed", quantity=3, accepted_at=T, supplier_confirmed_at=T, requester_confirmed_at=T)[-1]
        self.assertEqual(received["outcome"], {"arrived": 2, "failed": [{"din": "B", "reason": "expired_before_receipt"}], "from_ledger": True})

    def test_receipt_of_a_request_released_before_the_ledger_falls_back_to_request_unit_failures(self):
        failures = [{"din": "OLD-1", "reason": "expired_before_receipt"}]
        received = self.timeline(failures=failures, status="completed", quantity=3, supplier_confirmed_at=T, requester_confirmed_at=T)[-1]
        self.assertEqual(received["outcome"], {"arrived": 2, "failed": failures, "from_ledger": False})

    def test_receipt_with_nothing_failed_and_no_ledger_counts_everything_arrived(self):
        received = self.timeline(status="completed", quantity=3, supplier_confirmed_at=T, requester_confirmed_at=T)[-1]
        self.assertEqual(received["outcome"], {"arrived": 3, "failed": [], "from_ledger": False})

    def test_all_units_failed_means_none_arrived(self):
        units = [ledger_row("A", "failed", "expired_before_receipt"), ledger_row("B", "failed", "expired_before_receipt")]
        received = self.timeline(units=units, status="failed", quantity=2, supplier_confirmed_at=T, requester_confirmed_at=T)[-1]
        self.assertEqual((received["outcome"]["arrived"], len(received["outcome"]["failed"])), (0, 2))


class RequestDetailReadTests(TransitionBase):
    def detail(self, rid, facility=None):
        return app.get_request_detail(rid, facility_id=facility or self.hospital)

    def full_transfer(self, expiries=(5, 6, 30), quantity=3, receive_day=1):
        for n, days in enumerate(expiries):
            add(self.bank, f"{self.tag}-{n}", days)
        rid = self.new_request(quantity=quantity)
        self.accept(rid)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=receive_day)):
            app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        return rid

    def test_both_facilities_get_exactly_the_same_content(self):
        rid = self.full_transfer()
        self.assertEqual(self.detail(rid, self.hospital), self.detail(rid, self.bank))

    def test_the_message_count_is_stated_so_the_receipt_does_not_silently_omit_the_conversation(self):
        rid = self.new_request()
        self.assertEqual(self.detail(rid)["message_count"], 0)
        self.accept(rid)
        app.send_request_message(rid, app.SendRequestMessageBody(message="Ready at noon"), facility_id=self.bank)
        app.send_request_message(rid, app.SendRequestMessageBody(message="Thanks"), facility_id=self.hospital)
        self.assertEqual(self.detail(rid, self.bank)["message_count"], 2)
        self.assertEqual(self.detail(rid, self.hospital)["message_count"], 2)
        self.assertNotIn("messages", self.detail(rid))  # the count only: the thread itself is not part of the record

    def test_a_pending_request(self):
        d = self.detail(self.new_request())
        self.assertEqual(kinds(d["timeline"]), ["sent"])
        self.assertEqual((d["units"], d["units_recorded"]), ([], False))
        self.assertEqual(d["summary"], {"asked": 1, "received": None, "failed": None})

    def test_a_completed_transfer_reads_end_to_end_for_both_facilities(self):
        rid = self.full_transfer()
        for who in (self.hospital, self.bank):
            d = self.detail(rid, who)
            self.assertEqual(kinds(d["timeline"]), ["sent", "accepted", "released", "received"])
            by_kind = {e["kind"]: e for e in d["timeline"]}
            emails = {u: q("SELECT email FROM users WHERE id = :u", u=u)[0][0] for u in (self.bank_user, self.hospital_user)}
            self.assertEqual((by_kind["accepted"]["account_email"], by_kind["released"]["account_email"], by_kind["received"]["account_email"]),
                             (emails[self.bank_user], emails[self.bank_user], emails[self.hospital_user]))
            self.assertEqual(by_kind["released"]["units"], {"count": 3, "dins": [f"{self.tag}-0", f"{self.tag}-1", f"{self.tag}-2"],
                                                            "components": ["Packed RBC"]})
            self.assertEqual(by_kind["received"]["outcome"], {"arrived": 3, "failed": [], "from_ledger": True})
            self.assertEqual(d["summary"], {"asked": 3, "received": 3, "failed": 0})
            self.assertTrue(d["units_recorded"])
            self.assertEqual([u["outcome"] for u in d["units"]], ["received"] * 3)
            self.assertEqual(d["request"]["component"], "Packed RBC")
            self.assertEqual((d["request"]["requesting_facility"]["id"], d["request"]["supplying_facility"]["id"]), (self.hospital, self.bank))

    def test_a_partial_failure_reports_arrived_and_failed_with_the_reason(self):
        rid = self.full_transfer(expiries=(3, 5, 30), receive_day=5)
        d = self.detail(rid)
        received = next(e for e in d["timeline"] if e["kind"] == "received")
        self.assertEqual(received["outcome"]["arrived"], 2)
        self.assertEqual(received["outcome"]["failed"], [{"din": f"{self.tag}-0", "reason": "expired_before_receipt"}])
        self.assertEqual(d["summary"], {"asked": 3, "received": 2, "failed": 1})
        self.assertEqual({u["din"]: u["outcome"] for u in d["units"]},
                         {f"{self.tag}-0": "failed", f"{self.tag}-1": "received", f"{self.tag}-2": "received"})

    def test_the_unit_rows_carry_what_the_receipt_table_needs(self):
        rid = self.full_transfer(expiries=(5,), quantity=1)
        unit = self.detail(rid)["units"][0]
        self.assertEqual(set(unit), {"din", "blood_type", "component", "volume_ml", "collected_date", "expires_date", "origin_location",
                                     "days_remaining_at_release", "outcome", "reason", "days_remaining_at_receipt"})

    def test_declined_and_cancelled(self):
        declined, cancelled = self.new_request(), self.new_request()
        app.decline_request(declined, facility_id=self.bank, user_id=self.bank_user)
        app.cancel_request(cancelled, facility_id=self.hospital, user_id=self.hospital_user)
        self.assertEqual(kinds(self.detail(declined)["timeline"]), ["sent", "declined"])
        self.assertEqual(kinds(self.detail(cancelled)["timeline"]), ["sent", "cancelled"])

    def test_only_the_two_facilities_can_read_it_and_a_missing_request_is_a_404(self):
        rid = self.new_request()
        outsider_bank, _ = make_pair(self, self.tag + "O")
        with self.assertRaises(app.HTTPException) as ctx:
            self.detail(rid, outsider_bank)
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(app.HTTPException) as ctx:
            self.detail(2_000_000_000)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_historical_row_with_null_stamps_and_no_ledger_reads_with_its_gaps_shown(self):
        done = datetime(2026, 1, 1, 15, tzinfo=timezone.utc)
        with engine.begin() as c:
            rid = c.execute(text(
                "INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, "
                "created_at, supplier_confirmed_at, requester_confirmed_at) VALUES (:h, :b, 'O+', 3, 'restock', 'completed', :t, :s, :r) RETURNING id"),
                {"h": self.hospital, "b": self.bank, "t": datetime(2026, 1, 1, 9, tzinfo=timezone.utc), "s": done - timedelta(hours=1), "r": done}).scalar()
        d = self.detail(rid)
        self.assertEqual(kinds(d["timeline"]), ["sent", "accepted", "released", "received"])
        by_kind = {e["kind"]: e for e in d["timeline"]}
        self.assertIsNone(by_kind["accepted"]["at"])
        self.assertIsNone(by_kind["released"]["units"])
        self.assertTrue(all(e["account_email"] is None for e in d["timeline"]))
        self.assertEqual((d["units"], d["units_recorded"], d["request"]["component"]), ([], False, None))
        self.assertEqual(d["summary"], {"asked": 3, "received": 3, "failed": 0})

    def test_the_route_is_reachable_and_does_not_shadow_the_incoming_list(self):
        # GET /requests/incoming is a different route from GET /requests/{id}; both must still resolve to their own handler.
        paths = {(r.path, tuple(sorted(r.methods))) for r in app.app.routes if hasattr(r, "methods")}
        self.assertIn(("/requests/{request_id}", ("GET",)), paths)
        self.assertIn(("/requests/incoming", ("GET",)), paths)
        order = [r.path for r in app.app.routes if hasattr(r, "path")]
        self.assertLess(order.index("/requests/incoming"), order.index("/requests/{request_id}"))
