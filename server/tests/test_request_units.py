"""The per-unit ledger (request_units): what was released, as it stood, and what became of it.

Release writes one row per reserved unit, copied from the unit at that moment, in the same transaction as the
reservation. Receipt settles each row as received or failed (with a reason and the days left at receipt) in the same
transaction as the move. request_unit_failures is untouched and is written alongside; the two must agree.

What these tests prove: the writes and their transactions, that the snapshot is not rewritten by a CSV re-upload, that
deleting a unit from inventory leaves the row with its DIN, the three receipt outcomes, that the ledger and
request_unit_failures name the same failures, that nothing is written before release, and the table's own CHECKs.

What they do not prove: anything about production (scratch database only), concurrency between two releases, or how any
screen shows the ledger (there is none yet).

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import io
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import UploadFile

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import accepted_request, add

LEDGER = ("SELECT din, blood_type, component, volume_ml, collected_date, expires_date, origin_location, "
          "days_remaining_at_release, outcome, reason, days_remaining_at_receipt, blood_unit_id "
          "FROM request_units WHERE request_id = :r ORDER BY din")


def ledger(request_id):
    return q(LEDGER, r=request_id)


def receive_on(day_offset, request_id, hospital):
    with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=day_offset)):
        return app.confirm_receipt(request_id, facility_id=hospital, user_id=None)


class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def released(self, expiries, quantity=None):
        for n, days in enumerate(expiries):
            add(self.bank, self.din(n), days)
        rid = accepted_request(self.bank, self.hospital, quantity or len(expiries))
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        return rid


class ReleaseWritesTheLedgerTests(LedgerBase):
    def test_one_row_per_reserved_unit_snapshotting_the_unit_at_release(self):
        rid = self.released([5, 9])
        rows = ledger(rid)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first[:8], (self.din(0), "O+", "Packed RBC", 280, TODAY - timedelta(days=3),
                                     TODAY + timedelta(days=5), "Fridge A", 5))
        self.assertEqual(first[8:11], ("reserved", None, None))
        self.assertIsNotNone(first[11])
        self.assertEqual(rows[1][7], 9)

    def test_only_the_reserved_units_are_recorded_not_the_rest_of_the_stock(self):
        add(self.bank, self.din("SPARE"), 20)
        rid = self.released([5, 9])
        self.assertEqual([r[0] for r in ledger(rid)], [self.din(0), self.din(1)])

    def test_days_remaining_is_counted_from_the_business_day_of_release(self):
        add(self.bank, self.din(0), 6)
        rid = accepted_request(self.bank, self.hospital, 1)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=2)):
            app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(ledger(rid)[0][7], 4)

    def test_nothing_is_written_for_a_request_that_is_accepted_but_never_released(self):
        add(self.bank, self.din(0), 10)
        rid = accepted_request(self.bank, self.hospital, 1)
        self.assertEqual(ledger(rid), [])

    def test_a_release_that_fails_writes_nothing(self):
        add(self.bank, self.din(0), 10)
        rid = accepted_request(self.bank, self.hospital, 2)  # asks for two, one exists
        with self.assertRaises(app.HTTPException):
            app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(ledger(rid), [])

    def test_a_second_release_of_the_same_request_cannot_double_the_ledger(self):
        rid = self.released([5])
        with self.assertRaises(app.HTTPException):
            app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(len(ledger(rid)), 1)


class SnapshotSurvivesChangesTests(LedgerBase):
    def test_a_csv_reupload_that_rewrites_the_unit_does_not_rewrite_the_ledger(self):
        rid = self.released([5])
        before = ledger(rid)
        new_col, new_exp = TODAY - timedelta(days=1), TODAY + timedelta(days=90)
        csv_text = ("din,blood_type,component,location,volume_ml,collected_date,expires_date\n"
                    f"{self.din(0)},A+,Platelets,Shelf Z,450,{new_col},{new_exp}\n")
        asyncio.run(app.upload_inventory(
            file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="u.csv"), facility_id=self.bank, uploaded_by=None))

        live = q("SELECT blood_type, component, location, volume_ml, expires_date FROM blood_units WHERE din = :d", d=self.din(0))[0]
        self.assertEqual(live, ("A+", "Platelets", "Shelf Z", 450, new_exp))  # the upload really did rewrite the unit
        self.assertEqual(ledger(rid), before)                                  # and the ledger did not move

    def test_the_snapshot_is_what_receipt_settles_even_after_the_unit_changed(self):
        rid = self.released([5])
        asyncio.run(app.upload_inventory(
            file=UploadFile(file=io.BytesIO(
                ("din,blood_type,component,location,volume_ml,collected_date,expires_date\n"
                 f"{self.din(0)},O+,Packed RBC,Shelf Z,280,{TODAY},{TODAY + timedelta(days=5)}\n").encode()), filename="u.csv"),
            facility_id=self.bank, uploaded_by=None))
        receive_on(1, rid, self.hospital)
        row = ledger(rid)[0]
        self.assertEqual((row[6], row[8], row[10]), ("Fridge A", "received", 4))  # the supplier's shelf at release, not Shelf Z

    def reupload_expiry(self, din, days):
        csv_text = ("din,blood_type,component,location,volume_ml,collected_date,expires_date\n"
                    f"{din},O+,Packed RBC,Fridge A,280,{TODAY - timedelta(days=3)},{TODAY + timedelta(days=days)}\n")
        asyncio.run(app.upload_inventory(
            file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="u.csv"), facility_id=self.bank, uploaded_by=None))

    def test_days_at_receipt_follow_the_live_expiry_when_a_reupload_extends_it_in_transit(self):
        rid = self.released([5])
        self.reupload_expiry(self.din(0), 90)
        result = receive_on(1, rid, self.hospital)
        self.assertEqual(result["status"], "completed")
        expires, outcome, days_at_receipt = ledger(rid)[0][5], ledger(rid)[0][8], ledger(rid)[0][10]
        self.assertEqual(expires, TODAY + timedelta(days=5))  # the snapshot is still what was released
        self.assertEqual((outcome, days_at_receipt), ("received", 89))  # the figure is the live expiry's: 90 - 1
        self.assertGreater(days_at_receipt, (expires - (TODAY + timedelta(days=1))).days)  # and it exceeds what the snapshot would give (4)

    def test_days_at_receipt_follow_the_live_expiry_when_a_reupload_shortens_it_in_transit(self):
        # The case that matters: the snapshot says the unit was fine on the receipt day, the live date says it was not,
        # and the decision followed the live date. The ledger has to say so, not read as a unit that failed with time left.
        rid = self.released([5])
        self.reupload_expiry(self.din(0), 2)
        result = receive_on(4, rid, self.hospital)
        self.assertEqual((result["status"], result["units_transferred"]), ("failed", 0))
        row = ledger(rid)[0]
        self.assertEqual(row[5], TODAY + timedelta(days=5))  # snapshot: valid for another day at receipt
        self.assertEqual((row[8], row[9], row[10]), ("failed", "expired_before_receipt", -2))  # live: two days past expiry
        self.assertLess(row[10], (row[5] - (TODAY + timedelta(days=4))).days)  # below the snapshot's +1: the disagreement is visible
        # request_unit_failures records the live expiry too, so for a changed unit it differs from the ledger's snapshot date.
        self.assertEqual(q("SELECT expires_date FROM request_unit_failures WHERE request_id = :r", r=rid)[0][0], TODAY + timedelta(days=2))

    def test_the_figure_agrees_with_the_snapshot_when_nothing_changed(self):
        rid = self.released([5])
        receive_on(2, rid, self.hospital)
        row = ledger(rid)[0]
        self.assertEqual(row[10], (row[5] - (TODAY + timedelta(days=2))).days)

    def test_deleting_a_received_unit_leaves_the_ledger_row_with_its_din(self):
        rid = self.released([5])
        receive_on(1, rid, self.hospital)
        self.assertEqual(app.delete_inventory_unit(self.din(0), facility_id=self.hospital), {"deleted": True, "din": self.din(0)})

        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE din = :d", d=self.din(0))[0][0], 0)
        row = ledger(rid)[0]
        self.assertEqual((row[0], row[2], row[8], row[11]), (self.din(0), "Packed RBC", "received", None))  # DIN kept, unit link cleared

    def test_the_unit_link_is_kept_while_the_unit_exists(self):
        rid = self.released([5])
        receive_on(1, rid, self.hospital)
        unit_id = q("SELECT id FROM blood_units WHERE din = :d", d=self.din(0))[0][0]
        self.assertEqual(ledger(rid)[0][11], unit_id)


class ReceiptOutcomeTests(LedgerBase):
    def test_all_received(self):
        rid = self.released([5, 6, 30])
        result = receive_on(4, rid, self.hospital)
        self.assertEqual((result["status"], result["units_transferred"]), ("completed", 3))
        rows = ledger(rid)
        self.assertEqual([(r[8], r[9], r[10]) for r in rows], [("received", None, 1), ("received", None, 2), ("received", None, 26)])
        self.assertEqual(q("SELECT count(*) FROM request_unit_failures WHERE request_id = :r", r=rid)[0][0], 0)

    def test_partial_failure_marks_only_the_expired_unit_and_records_the_days_it_was_late(self):
        rid = self.released([3, 5, 30])
        result = receive_on(5, rid, self.hospital)
        self.assertEqual((result["status"], result["units_transferred"]), ("completed", 2))
        rows = ledger(rid)
        self.assertEqual([(r[0], r[8], r[9], r[10]) for r in rows],
                         [(self.din(0), "failed", "expired_before_receipt", -2),
                          (self.din(1), "received", None, 0),     # expires on the receipt day: still usable, zero days left
                          (self.din(2), "received", None, 25)])

    def test_all_failed(self):
        rid = self.released([3, 3])
        result = receive_on(6, rid, self.hospital)
        self.assertEqual((result["status"], result["units_transferred"]), ("failed", 0))
        self.assertEqual([(r[8], r[9], r[10]) for r in ledger(rid)], [("failed", "expired_before_receipt", -3)] * 2)

    def test_the_ledger_and_request_unit_failures_agree_on_failures(self):
        for expiries, day in (([3, 5, 30], 5), ([3, 3], 6), ([5, 6], 4)):
            with self.subTest(expiries=expiries):
                tag = uuid.uuid4().hex[:6].upper()
                bank, hospital = make_pair(self, tag)
                for n, days in enumerate(expiries):
                    add(bank, f"{tag}-{n}", days)
                rid = accepted_request(bank, hospital, len(expiries))
                app.confirm_release(rid, facility_id=bank, user_id=None)
                receive_on(day, rid, hospital)
                in_ledger = q("SELECT din, expires_date, reason FROM request_units WHERE request_id = :r AND outcome = 'failed' ORDER BY din", r=rid)
                in_failures = q("SELECT din, expires_date, reason FROM request_unit_failures WHERE request_id = :r ORDER BY din", r=rid)
                self.assertEqual(in_ledger, in_failures)
                received = q("SELECT din FROM request_units WHERE request_id = :r AND outcome = 'received' ORDER BY din", r=rid)
                moved = q("SELECT din FROM blood_units WHERE facility_id = :h ORDER BY din", h=hospital)
                self.assertEqual(received, moved)  # what the ledger calls received is what is now at the requester

    def test_every_row_is_settled_and_none_is_left_reserved(self):
        rid = self.released([3, 5, 30])
        receive_on(5, rid, self.hospital)
        self.assertEqual(q("SELECT count(*) FROM request_units WHERE request_id = :r AND outcome = 'reserved'", r=rid)[0][0], 0)

    def test_a_request_released_before_the_ledger_existed_is_received_as_before_and_gets_no_invented_rows(self):
        rid = self.released([5, 6])
        with engine.begin() as c:
            c.execute(text("DELETE FROM request_units WHERE request_id = :r"), {"r": rid})  # what such a request looks like
        result = receive_on(1, rid, self.hospital)
        self.assertEqual((result["status"], result["units_transferred"]), ("completed", 2))
        self.assertEqual(ledger(rid), [])


class LedgerConstraintTests(LedgerBase):
    def bad(self, sql, **params):
        self.setUp()  # a fresh pair each time: a DIN is unique across the table
        rid = self.released([5])
        with self.assertRaises(IntegrityError), engine.begin() as c:
            c.execute(text(sql), {"r": rid, **params})

    def test_failed_needs_the_expiry_reason(self):
        self.bad("UPDATE request_units SET outcome = 'failed', days_remaining_at_receipt = -1 WHERE request_id = :r")

    def test_a_reason_without_failure_is_refused(self):
        self.bad("UPDATE request_units SET outcome = 'received', reason = 'expired_before_receipt', days_remaining_at_receipt = 1 WHERE request_id = :r")

    def test_a_settled_row_needs_its_days_at_receipt_and_a_reserved_row_must_not_have_them(self):
        self.bad("UPDATE request_units SET outcome = 'received' WHERE request_id = :r")
        self.bad("UPDATE request_units SET days_remaining_at_receipt = 2 WHERE request_id = :r")

    def test_an_unknown_outcome_is_refused(self):
        self.bad("UPDATE request_units SET outcome = 'lost', days_remaining_at_receipt = 1 WHERE request_id = :r")

    def test_a_unit_cannot_be_in_one_request_twice(self):
        self.bad("INSERT INTO request_units (request_id, blood_unit_id, din, blood_type, component, volume_ml, collected_date, "
                 "expires_date, origin_location, days_remaining_at_release) "
                 "SELECT request_id, blood_unit_id, din, blood_type, component, volume_ml, collected_date, expires_date, "
                 "origin_location, days_remaining_at_release FROM request_units WHERE request_id = :r")


if __name__ == "__main__":
    unittest.main()
