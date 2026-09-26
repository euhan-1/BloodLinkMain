"""Bulk CSV upload paths (historical stock, inventory, donors) against the real DB,
on two disposable facilities that are deleted afterwards.

Regression guard for the 502: these endpoints used to issue one INSERT per row,
~75 ms each against the remote DB, so 1,440 rows took ~107 s. The main assertion
is a deterministic statement-count ceiling (independent of network speed); the
time bound is deliberately loose. Also checks the behaviour the batching must not
lose: per-row errors with line numbers, upload_history_id tagging, undo, and
update-not-duplicate on re-upload.

Run from server/: .venv\\Scripts\\python.exe -m unittest tests.test_bulk_upload -v
"""
import asyncio
import io
import time
import unittest
import uuid
from datetime import date, timedelta

from sqlalchemy import event, text
from starlette.datastructures import UploadFile

import main as app
from database import engine

TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
MAX_STATEMENTS = 15  # fixed overhead is ~6; the old code was ~rows + 6
MAX_SECONDS = 30  # old code: ~107 s for 1,440 rows


class Counter:
    n = 0

    def __call__(self, *a):
        self.n += 1


def upload(fn, fid, csv_text, name="f.csv"):
    """Runs an upload handler, returns (body, statements, seconds)."""
    counter = Counter()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        t0 = time.time()
        body = asyncio.run(fn(file=UploadFile(file=io.BytesIO(csv_text.encode()), filename=name), facility_id=fid, uploaded_by=None))
        return body, counter.n, time.time() - t0
    finally:
        event.remove(engine, "before_cursor_execute", counter)


def q(sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


class BulkUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tag = uuid.uuid4().hex[:6].upper()
        with engine.begin() as c:
            cls.fid, cls.other = [
                c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                               "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"BulkTest {cls.tag} {i}"}).scalar()
                for i in (1, 2)
            ]

    @classmethod
    def tearDownClass(cls):
        with engine.begin() as c:
            for f in (cls.fid, cls.other):
                for t in ("notifications", "blood_units", "donors", "inventory_snapshots", "upload_history"):
                    c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": f})
                c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": f})

    def test_historical_1440_rows(self):
        start = date.today() - timedelta(days=180)
        lines = ["snapshot_date,blood_type,units"]
        lines += [f"{start + timedelta(days=d)},{t},{50 + d % 30}" for d in range(180) for t in TYPES]
        lines.insert(5, "not-a-date,O+,10")  # bad row on file line 6
        body, stmts, secs = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(lines))
        self.assertEqual(body["rows_processed"], 1440)
        self.assertEqual(body["errors"], [{"row": 6, "reason": "invalid snapshot_date 'not-a-date', expected YYYY-MM-DD"}])
        self.assertEqual(body["days_of_history"], 180)
        self.assertLessEqual(stmts, MAX_STATEMENTS, f"{stmts} statements for 1,440 rows: not batched")
        self.assertLess(secs, MAX_SECONDS)
        uid = q("SELECT id, rows_processed, rows_failed, raw_content FROM upload_history WHERE facility_id=:f AND upload_type='historical_stock'", f=self.fid)[0]
        self.assertEqual((uid[1], uid[2]), (1440, 1))
        self.assertIn("not-a-date", uid[3])  # raw_content kept for undo/audit
        self.assertEqual(q("SELECT count(*) FROM inventory_snapshots WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=uid[0])[0][0], 1440)

        # re-upload the same days with new values: update in place, no duplicates
        again = "snapshot_date,blood_type,units\n" + "\n".join(f"{start + timedelta(days=d)},{t},7" for d in range(180) for t in TYPES)
        body, stmts, _ = upload(app.upload_historical_inventory_snapshots, self.fid, again)
        self.assertEqual(body["rows_processed"], 1440)
        self.assertLessEqual(stmts, MAX_STATEMENTS)
        self.assertEqual(q("SELECT count(*), min(units), max(units) FROM inventory_snapshots WHERE facility_id=:f", f=self.fid)[0], (1440, 7, 7))

    def test_historical_duplicate_key_in_file_last_wins(self):
        d = date.today() - timedelta(days=1)
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.other, f"snapshot_date,blood_type,units\n{d},O+,10\n{d},O+,99")
        self.assertEqual(body["errors"], [])
        self.assertEqual(q("SELECT units FROM inventory_snapshots WHERE facility_id=:f AND blood_type='O+'", f=self.other), [(99,)])

    def test_inventory_1200_rows_errors_ownership_and_undo(self):
        exp = date.today() + timedelta(days=30)
        col = date.today() - timedelta(days=5)
        lines = ["din,blood_type,component,location,volume_ml,collected_date,expires_date"]
        lines += [f"BT{self.tag}-{i},{TYPES[i % 8]},Packed RBC,Bay 1,280,{col},{exp}" for i in range(1200)]
        lines.insert(3, f"BT{self.tag}-BAD,XX,Packed RBC,Bay 1,280,{col},{exp}")  # invalid type, file line 4
        body, stmts, secs = upload(app.upload_inventory, self.fid, "\n".join(lines))
        self.assertEqual(body["rows_processed"], 1200)
        self.assertEqual(body["errors"], [{"row": 4, "reason": "invalid blood_type 'XX'"}])
        self.assertLessEqual(stmts, MAX_STATEMENTS)
        self.assertLess(secs, MAX_SECONDS)
        (uid,) = q("SELECT id FROM upload_history WHERE facility_id=:f AND upload_type='inventory'", f=self.fid)[0]
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=uid)[0][0], 1200)

        # a DIN owned by another facility is rejected per row (with its line number); the rest of the file imports
        stolen = f"BT{self.tag}-0"
        mine = f"BT{self.tag}-NEW"
        csv2 = ("din,blood_type,component,location,volume_ml,collected_date,expires_date\n"
                f"{stolen},O+,Packed RBC,Bay 1,280,{col},{exp}\n{mine},O+,Packed RBC,Bay 1,280,{col},{exp}")
        body, _, _ = upload(app.upload_inventory, self.other, csv2)
        self.assertEqual(body["rows_processed"], 1)
        self.assertEqual(body["errors"], [{"row": 2, "reason": f"DIN '{stolen}' is already registered to a different facility"}])
        self.assertEqual(q("SELECT facility_id FROM blood_units WHERE din=:d", d=stolen), [(self.fid,)])

        # undo removes exactly this upload's units
        result = app.undo_upload(uid, facility_id=self.fid)
        self.assertEqual(result["removed_count"], 1200)
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id=:f", f=self.fid)[0][0], 0)

    def test_donors_1000_rows_update_not_duplicate_and_undo(self):
        lines = ["name,blood_type,phone"] + [f"Donor {i},{TYPES[i % 8]},09{self.tag[:2]}{i:07d}" for i in range(1000)]
        lines.insert(2, "No Type,ZZ,0999")  # file line 3
        body, stmts, secs = upload(app.upload_donors, self.fid, "\n".join(lines))
        self.assertEqual(body["rows_processed"], 1000)
        self.assertEqual(body["errors"], [{"row": 3, "reason": "invalid blood_type 'ZZ'"}])
        self.assertLessEqual(stmts, MAX_STATEMENTS)
        self.assertLess(secs, MAX_SECONDS)
        (uid,) = q("SELECT id FROM upload_history WHERE facility_id=:f AND upload_type='donors'", f=self.fid)[0]
        self.assertIsNone(q("SELECT raw_content FROM upload_history WHERE id=:u", u=uid)[0][0])  # donor PII is never kept
        self.assertEqual(q("SELECT count(*) FROM donors WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=uid)[0][0], 1000)

        body, _, _ = upload(app.upload_donors, self.fid, "name,blood_type,phone\nRenamed,O-," + f"09{self.tag[:2]}{0:07d}")
        self.assertEqual(body["rows_processed"], 1)
        self.assertEqual(q("SELECT count(*) FROM donors WHERE facility_id=:f", f=self.fid)[0][0], 1000)
        self.assertEqual(q("SELECT name FROM donors WHERE facility_id=:f AND phone=:p", f=self.fid, p=f"09{self.tag[:2]}{0:07d}"), [("Renamed",)])


if __name__ == "__main__":
    unittest.main()
