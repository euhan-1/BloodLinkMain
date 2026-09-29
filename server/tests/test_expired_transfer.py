"""A reserved unit that expires between confirm-release and confirm-receipt.

Before: confirm_receipt moved every reserved unit with no expiry check, so an
expired unit was transferred to the requester. Now it fails per unit: not
moved, reservation cleared, archived at the supplier, recorded in
request_unit_failures, and both facilities are notified with its DIN. Also
covers the reserved-unit guard on archive_expired_inventory and the undo of an
upload whose unit failed in transfer.

"Today" is moved forward with mock.patch between release and receipt — the
real scenario is a day passing while the units are in transit.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import io
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import text
from starlette.datastructures import UploadFile

import main as app
from database import engine

TODAY = app.business_today()


def q(sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def make_pair(test, tag):
    """A searchable bank + hospital (coordinates set, like any completed
    profile), deleted after the test so they never leak into other tests'
    nearby searches."""
    with engine.begin() as c:
        ids = [
            c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude) "
                           "VALUES (:n, :t, true, true, 14.6, 121.0) RETURNING id"), {"n": f"ExpXfer {tag} {t}", "t": t}).scalar()
            for t in ("bloodbank", "hospital")
        ]
    test.addCleanup(delete_facilities, ids)
    return ids


def delete_facilities(ids):
    reqs = "(SELECT id FROM requests WHERE requesting_facility_id = ANY(:f) OR supplying_facility_id = ANY(:f))"
    with engine.begin() as c:
        for sql in (
            f"DELETE FROM request_unit_failures WHERE request_id IN {reqs}",
            f"DELETE FROM request_messages WHERE request_id IN {reqs}",
            "DELETE FROM blood_units WHERE facility_id = ANY(:f)",
            "DELETE FROM requests WHERE requesting_facility_id = ANY(:f) OR supplying_facility_id = ANY(:f)",
            "DELETE FROM notifications WHERE facility_id = ANY(:f)",
            "DELETE FROM inventory_snapshots WHERE facility_id = ANY(:f)",
            "DELETE FROM upload_history WHERE facility_id = ANY(:f)",
            "DELETE FROM facility_forecast_cache WHERE facility_id = ANY(:f)",
            "DELETE FROM forecast_alert_state WHERE facility_id = ANY(:f)",
            "DELETE FROM blood_type_thresholds WHERE facility_id = ANY(:f)",
            "DELETE FROM facilities WHERE id = ANY(:f)",
        ):
            c.execute(text(sql), {"f": ids})


def add_unit(facility_id, din, expires_in_days):
    with engine.begin() as c:
        c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, "
                       "expires_date, facility_id) VALUES (:d, 'O+', 'Packed RBC', 'Fridge A', 280, :col, :exp, :f)"),
                  {"d": din, "col": TODAY - timedelta(days=30), "exp": TODAY + timedelta(days=expires_in_days), "f": facility_id})


def released_request(bank, hospital, quantity):
    req = app.create_request(app.CreateRequestBody(supplying_facility_id=bank, blood_type="O+", quantity=quantity,
                                                   emergency_type="trauma"), requesting_facility_id=hospital)
    app.accept_request(req["id"], facility_id=bank)
    app.confirm_release(req["id"], facility_id=bank)
    return req["id"]


def receive_on(day_offset, request_id, hospital):
    with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=day_offset)):
        return app.confirm_receipt(request_id, facility_id=hospital)


class ExpiredBeforeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def test_partial_receipt_transfers_usable_units_and_fails_the_expired_one(self):
        add_unit(self.bank, self.din(1), 1)   # expired by receipt day
        add_unit(self.bank, self.din(2), 2)   # expires ON receipt day: still usable
        add_unit(self.bank, self.din(3), 30)
        rid = released_request(self.bank, self.hospital, 3)

        result = receive_on(2, rid, self.hospital)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["units_transferred"], 2)
        self.assertEqual([u["din"] for u in result["failed_units"]], [self.din(1)])
        self.assertEqual({r[0] for r in q("SELECT din FROM blood_units WHERE facility_id = :f", f=self.hospital)},
                         {self.din(2), self.din(3)})
        # The failed unit stays at the supplier, unreserved and archived — never deleted.
        facility, reserved, archived = q("SELECT facility_id, reserved_for_request_id, archived_at FROM blood_units "
                                         "WHERE din = :d", d=self.din(1))[0]
        self.assertEqual((facility, reserved), (self.bank, None))
        self.assertIsNotNone(archived)
        self.assertEqual(q("SELECT din, expires_date, reason FROM request_unit_failures WHERE request_id = :r", r=rid),
                         [(self.din(1), TODAY + timedelta(days=1), "expired_before_receipt")])
        # Both sides are told which unit and why.
        for f in (self.bank, self.hospital):
            msgs = [m for (m,) in q("SELECT message FROM notifications WHERE facility_id = :f "
                                    "AND type = 'transfer_units_failed'", f=f)]
            self.assertEqual(len(msgs), 1)
            self.assertIn(self.din(1), msgs[0])
            self.assertIn("2 of 3", msgs[0])
        listed = next(r for r in app.list_requests(facility_id=self.hospital) if r["id"] == rid)
        self.assertEqual([u["din"] for u in listed["failed_units"]], [self.din(1)])
        incoming = next(r for r in app.list_incoming_requests(facility_id=self.bank) if r["id"] == rid)
        self.assertEqual(incoming["failed_units"], listed["failed_units"])

    def test_every_unit_expired_fails_the_request_and_moves_nothing(self):
        add_unit(self.bank, self.din(1), 1)
        add_unit(self.bank, self.din(2), 1)
        rid = released_request(self.bank, self.hospital, 2)

        result = receive_on(3, rid, self.hospital)

        self.assertEqual((result["status"], result["units_transferred"], len(result["failed_units"])), ("failed", 0, 2))
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id = :f", f=self.hospital)[0][0], 0)
        status, confirmed = q("SELECT status, requester_confirmed_at FROM requests WHERE id = :r", r=rid)[0]
        self.assertEqual(status, "failed")
        self.assertIsNotNone(confirmed)
        # Still a terminal state: a second confirm can't re-run it.
        with self.assertRaises(app.HTTPException):
            receive_on(3, rid, self.hospital)
        # The two facilities can still talk about it.
        app.send_request_message(rid, app.SendRequestMessageBody(message="Sending replacements"), facility_id=self.bank)

    def test_nothing_expired_is_unchanged_behaviour(self):
        add_unit(self.bank, self.din(1), 10)
        rid = released_request(self.bank, self.hospital, 1)

        result = receive_on(1, rid, self.hospital)

        self.assertEqual((result["status"], result["units_transferred"], result["failed_units"]), ("completed", 1, []))
        self.assertEqual(q("SELECT type FROM notifications WHERE facility_id = :f ORDER BY id DESC LIMIT 1",
                           f=self.bank)[0][0], "transfer_completed")
        self.assertEqual(q("SELECT count(*) FROM request_unit_failures WHERE request_id = :r", r=rid)[0][0], 0)


class ArchiveSkipsReservedUnitsTests(unittest.TestCase):
    def test_expired_reserved_unit_is_not_archived_then_fails_at_receipt(self):
        tag = uuid.uuid4().hex[:6].upper()
        bank, hospital = make_pair(self, tag)
        add_unit(bank, f"{tag}-R", 1)
        rid = released_request(bank, hospital, 1)
        add_unit(bank, f"{tag}-FREE", 1)  # expired too, but not reserved

        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=2)):
            self.assertEqual(app.archive_expired_inventory(facility_id=bank)["archived_count"], 1)
        archived = dict(q("SELECT din, archived_at IS NOT NULL FROM blood_units WHERE facility_id = :f", f=bank))
        self.assertEqual(archived, {f"{tag}-R": False, f"{tag}-FREE": True})

        # The request still settles it: recorded as failed, and only then archived.
        self.assertEqual(receive_on(2, rid, hospital)["status"], "failed")
        self.assertTrue(q("SELECT archived_at IS NOT NULL FROM blood_units WHERE din = :d", d=f"{tag}-R")[0][0])


class UndoAfterFailedTransferTests(unittest.TestCase):
    def test_undo_leaves_a_unit_that_failed_in_transfer(self):
        tag = uuid.uuid4().hex[:6].upper()
        bank, hospital = make_pair(self, tag)
        col = TODAY - timedelta(days=2)
        csv_text = "din,blood_type,component,location,volume_ml,collected_date,expires_date\n" + "\n".join(
            f"{tag}-{n},O+,Whole Blood,Fridge A,450,{col},{TODAY + timedelta(days=d)}" for n, d in [(1, 1), (2, 30)])
        asyncio.run(app.upload_inventory(
            file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="u.csv"), facility_id=bank, uploaded_by=None))
        upload_id = q("SELECT id FROM upload_history WHERE facility_id = :f", f=bank)[0][0]
        rid = released_request(bank, hospital, 1)  # FEFO reserves -1
        receive_on(2, rid, hospital)

        result = app.undo_upload(upload_id, facility_id=bank)

        self.assertEqual(result["counts"], {"created_removed": 1, "modified_left_unchanged": 0, "skipped_transferred": 1})
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE din = :d", d=f"{tag}-1")[0][0], 1)


if __name__ == "__main__":
    unittest.main()
