"""When each request transition happened, and which account did it.

accept, decline, cancel, release (confirm-release) and receipt (confirm-receipt) each stamp their own timestamp and actor
on the requests row, with now() and the acting user's id, in the same transaction as the status change. Release and
receipt use the existing supplier_confirmed_at / requester_confirmed_at, with released_by / received_by beside them.

The actor is the logged-in account (get_acting_user_id), which at BloodLink is one account per facility: these tests prove
the right facility's account is recorded, not that a person is identified. Nothing is backfilled; a row from before these
columns has NULLs, which read as "not recorded".

What these tests prove: each transition writes its own pair and no other, a refused or failed transition writes nothing,
an atomic failure after the update rolls the stamp back too, and rows with NULL stamps still read and still transition.

What they do not prove: anything about production, or concurrency (two calls racing on one request).

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

STAMPS = ("accepted_at", "accepted_by", "declined_at", "declined_by", "cancelled_at", "cancelled_by",
          "supplier_confirmed_at", "released_by", "requester_confirmed_at", "received_by")


def make_user(facility_id):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) "
                              "VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                         {"e": f"transition-{uuid.uuid4().hex}@example.test", "f": facility_id}).scalar()


class TransitionBase(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)  # its cleanup also deletes the users made below
        self.bank_user, self.hospital_user = make_user(self.bank), make_user(self.hospital)

    def new_request(self, **over):
        body = dict(supplying_facility_id=self.bank, blood_type="O+", component="Packed RBC", quantity=1, emergency_type="trauma")
        return app.create_request(app.CreateRequestBody(**{**body, **over}), requesting_facility_id=self.hospital)["id"]

    def stamps(self, rid):
        row = q(f"SELECT {', '.join(STAMPS)} FROM requests WHERE id = :r", r=rid)[0]
        return dict(zip(STAMPS, row))

    def only(self, rid, *names):
        """Exactly these stamps are set, and no others."""
        got = {k for k, v in self.stamps(rid).items() if v is not None}
        self.assertEqual(got, set(names), self.stamps(rid))

    def accept(self, rid):
        app.accept_request(rid, facility_id=self.bank, user_id=self.bank_user)

    def release(self, rid):
        add(self.bank, f"{self.tag}-{uuid.uuid4().hex[:6]}", 10)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)


class EachTransitionStampsItsOwnPairTests(TransitionBase):
    def test_nothing_is_stamped_when_a_request_is_created(self):
        self.only(self.new_request())

    def test_accept(self):
        rid = self.new_request()
        self.accept(rid)
        self.only(rid, "accepted_at", "accepted_by")
        self.assertEqual(self.stamps(rid)["accepted_by"], self.bank_user)

    def test_decline(self):
        rid = self.new_request()
        app.decline_request(rid, facility_id=self.bank, user_id=self.bank_user)
        self.only(rid, "declined_at", "declined_by")
        self.assertEqual(self.stamps(rid)["declined_by"], self.bank_user)

    def test_cancel(self):
        rid = self.new_request()
        app.cancel_request(rid, facility_id=self.hospital, user_id=self.hospital_user)
        self.only(rid, "cancelled_at", "cancelled_by")
        self.assertEqual(self.stamps(rid)["cancelled_by"], self.hospital_user)

    def test_release_adds_only_its_own_pair_and_leaves_earlier_stamps_alone(self):
        rid = self.new_request()
        self.accept(rid)
        before = self.stamps(rid)
        self.release(rid)
        self.only(rid, "accepted_at", "accepted_by", "supplier_confirmed_at", "released_by")
        after = self.stamps(rid)
        self.assertEqual((after["accepted_at"], after["accepted_by"]), (before["accepted_at"], before["accepted_by"]))
        self.assertEqual(after["released_by"], self.bank_user)

    def test_receipt_adds_only_its_own_pair_and_leaves_earlier_stamps_alone(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        before = self.stamps(rid)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        self.only(rid, "accepted_at", "accepted_by", "supplier_confirmed_at", "released_by", "requester_confirmed_at", "received_by")
        after = self.stamps(rid)
        for k in ("accepted_at", "accepted_by", "supplier_confirmed_at", "released_by"):
            self.assertEqual(after[k], before[k], k)
        self.assertEqual(after["received_by"], self.hospital_user)

    def test_a_failed_receipt_still_stamps_who_confirmed_it(self):
        add(self.bank, f"{self.tag}-X", 3)
        rid = self.new_request()
        self.accept(rid)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=9)):
            result = app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.stamps(rid)["received_by"], self.hospital_user)

    def test_timestamps_are_the_database_clock_in_order(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        s = self.stamps(rid)
        self.assertLessEqual(s["accepted_at"], s["supplier_confirmed_at"])
        self.assertLessEqual(s["supplier_confirmed_at"], s["requester_confirmed_at"])
        self.assertLess(datetime.now(timezone.utc) - s["requester_confirmed_at"], timedelta(minutes=2))

    def test_no_user_behind_the_call_still_stamps_the_time_with_an_empty_actor(self):
        # The dev-only facility override has no real user: get_acting_user_id returns None.
        rid = self.new_request()
        app.accept_request(rid, facility_id=self.bank, user_id=None)
        s = self.stamps(rid)
        self.assertIsNotNone(s["accepted_at"])
        self.assertIsNone(s["accepted_by"])


class AFailedTransitionWritesNeitherTests(TransitionBase):
    def refused(self, call, status_code):
        with self.assertRaises(app.HTTPException) as ctx:
            call()
        self.assertEqual(ctx.exception.status_code, status_code)

    def test_the_wrong_facility_cannot_accept_decline_cancel_release_or_receive(self):
        rid = self.new_request()
        self.refused(lambda: app.accept_request(rid, facility_id=self.hospital, user_id=self.hospital_user), 403)
        self.refused(lambda: app.decline_request(rid, facility_id=self.hospital, user_id=self.hospital_user), 403)
        self.refused(lambda: app.cancel_request(rid, facility_id=self.bank, user_id=self.bank_user), 403)
        self.only(rid)
        self.accept(rid)
        self.refused(lambda: app.confirm_release(rid, facility_id=self.hospital, user_id=self.hospital_user), 403)
        self.refused(lambda: app.confirm_receipt(rid, facility_id=self.bank, user_id=self.bank_user), 403)
        self.only(rid, "accepted_at", "accepted_by")

    def test_a_transition_out_of_the_wrong_status_writes_nothing(self):
        rid = self.new_request()
        app.decline_request(rid, facility_id=self.bank, user_id=self.bank_user)
        declined = self.stamps(rid)
        self.refused(lambda: app.accept_request(rid, facility_id=self.bank, user_id=self.bank_user), 400)
        self.refused(lambda: app.cancel_request(rid, facility_id=self.hospital, user_id=self.hospital_user), 400)
        self.assertEqual(self.stamps(rid), declined)

        accepted = self.new_request()
        self.accept(accepted)
        before = self.stamps(accepted)
        self.refused(lambda: app.decline_request(accepted, facility_id=self.bank, user_id=self.bank_user), 400)
        self.refused(lambda: app.cancel_request(accepted, facility_id=self.hospital, user_id=self.hospital_user), 400)
        self.assertEqual(self.stamps(accepted), before)

    def test_receipt_before_release_and_release_with_no_stock_write_nothing(self):
        rid = self.new_request()
        self.accept(rid)
        self.refused(lambda: app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user), 400)
        self.refused(lambda: app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user), 400)  # no stock at all
        self.only(rid, "accepted_at", "accepted_by")

    def test_a_second_release_or_receipt_does_not_overwrite_the_first(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        before = self.stamps(rid)
        other_bank_user = make_user(self.bank)
        self.refused(lambda: app.confirm_release(rid, facility_id=self.bank, user_id=other_bank_user), 400)
        self.refused(lambda: app.confirm_receipt(rid, facility_id=self.hospital, user_id=other_bank_user), 400)
        self.assertEqual(self.stamps(rid), before)

    def test_a_failure_after_the_update_rolls_the_stamp_back_with_the_status(self):
        # The stamp and the status change are one transaction: if anything later in it fails, neither lands.
        for name, user, facility in (("accept_request", self.bank_user, self.bank), ("decline_request", self.bank_user, self.bank),
                                     ("cancel_request", self.hospital_user, self.hospital)):
            with self.subTest(name):
                rid = self.new_request()
                with mock.patch.object(app, "_create_notification", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        getattr(app, name)(rid, facility_id=facility, user_id=user)
                self.only(rid)
                self.assertEqual(q("SELECT status FROM requests WHERE id = :r", r=rid)[0][0], "pending")

    def test_a_failure_after_release_or_receipt_updates_rolls_back_the_stamp_the_reservation_and_the_move(self):
        rid = self.new_request()
        self.accept(rid)
        din = f"{self.tag}-ROLL"
        add(self.bank, din, 10)
        with mock.patch.object(app, "_create_notification", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        self.only(rid, "accepted_at", "accepted_by")
        self.assertIsNone(q("SELECT reserved_for_request_id FROM blood_units WHERE din = :d", d=din)[0][0])

        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        released = self.stamps(rid)
        with mock.patch.object(app, "_create_notification", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        self.assertEqual(self.stamps(rid), released)
        self.assertEqual(q("SELECT facility_id FROM blood_units WHERE din = :d", d=din)[0][0], self.bank)


class HistoricalRowsWithNullsTests(TransitionBase):
    """Rows from before these columns existed: every stamp NULL, in every status."""

    def seed(self):
        made = {}
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with engine.begin() as c:
            for i, status in enumerate(("pending", "accepted", "declined", "cancelled", "completed", "failed")):
                done = t + timedelta(hours=i)
                settled = status in ("completed", "failed")
                made[status] = c.execute(text(
                    "INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, "
                    "status, created_at, supplier_confirmed_at, requester_confirmed_at) "
                    "VALUES (:h, :b, 'O+', 1, 'restock', :s, :t, :sc, :rc) RETURNING id"),
                    {"h": self.hospital, "b": self.bank, "s": status, "t": t + timedelta(hours=i),
                     "sc": done if settled else None, "rc": done if settled else None}).scalar()
        return made

    def test_every_status_with_null_stamps_reads_through_both_lists_and_the_chat(self):
        made = self.seed()
        new_columns = [k for k in STAMPS if k not in ("supplier_confirmed_at", "requester_confirmed_at")]
        for rid in made.values():
            self.assertTrue(all(self.stamps(rid)[k] is None for k in new_columns))
        mine = {r["id"] for r in app.list_requests(facility_id=self.hospital)}
        theirs = {r["id"] for r in app.list_incoming_requests(facility_id=self.bank)}
        self.assertEqual(mine, set(made.values()))
        self.assertEqual(theirs, set(made.values()))
        for rid in made.values():
            self.assertEqual(app.list_request_messages(rid, facility_id=self.hospital), [])

    def test_a_historical_pending_request_can_still_be_accepted_and_released_and_gets_stamps_from_then_on(self):
        rid = self.seed()["pending"]
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET component = 'Packed RBC' WHERE id = :r"), {"r": rid})  # components are stage 1's concern
        self.accept(rid)
        self.release(rid)
        self.only(rid, "accepted_at", "accepted_by", "supplier_confirmed_at", "released_by")

    def test_historical_rows_are_not_touched_by_a_later_transition_on_another_request(self):
        made = self.seed()
        before = {rid: self.stamps(rid) for rid in made.values()}
        rid = self.new_request()
        self.accept(rid)
        self.assertEqual({r: self.stamps(r) for r in made.values()}, before)


if __name__ == "__main__":
    unittest.main()
