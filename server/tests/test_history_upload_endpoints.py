"""POST /forecast/historical-upload/preview and the save (POST /forecast/historical-upload) for .csv and .xlsx, long and wide, through the real
ASGI app, on the scratch database (conftest.py): the preview writes nothing, the save writes exactly what the preview counted, an
ambiguous date file is refused until a choice is sent, hospitals get 403, an oversize file 413, and an undo still removes the rows.
"""
import asyncio
import io
import json
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from openpyxl import Workbook
from sqlalchemy import text

import auth
import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair
from tests.test_upload_endpoints import asgi

BLOOD = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
TABLES = ("inventory_snapshots", "inventory_snapshot_write_log", "upload_history", "facility_forecast_cache")


def multipart(filename, data: bytes):
    boundary = "xBOUNDARYx"
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def days_ago(n):
    return TODAY - timedelta(days=n)


def long_csv(days=120):
    lines = ["Date,Blood Group,Quantity"]
    for d in range(1, days + 1):
        lines += [f"{days_ago(d)},{bt},{10 + d % 7}" for bt in BLOOD]
    return "\n".join(lines).encode()


class HistoryUploadEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        self.tokens = {}
        with engine.begin() as c:
            for who, fid in (("bank", self.bank), ("hospital", self.hospital)):
                email = f"hist-{who}-{self.tag}@example.test"
                uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                {"e": email, "f": fid}).scalar()
                self.tokens[who] = [("authorization", "Bearer " + auth.create_access_token(user_id=uid, email=email, facility_id=fid, role="staff"))]
        self.addCleanup(self.clear)

    def clear(self):
        with engine.begin() as c:
            for t in ("inventory_snapshot_write_log", "inventory_snapshots", "upload_history", "facility_forecast_cache"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = ANY(:f)"), {"f": [self.bank, self.hospital]})

    def post(self, path, filename, data, who="bank", query=""):
        body, ctype = multipart(filename, data)
        path, _, query = path.partition("?")
        status, raw = asyncio.run(asgi("POST", path, [*self.tokens[who], ("content-type", ctype)], body, query.encode()))
        return status, json.loads(raw)

    def preview(self, filename, data, who="bank", order=None):
        return self.post("/forecast/historical-upload/preview" + (f"?date_order={order}" if order else ""), filename, data, who)

    def save(self, filename, data, who="bank", order=None):
        return self.post("/forecast/historical-upload" + (f"?date_order={order}" if order else ""), filename, data, who)

    def counts(self):
        with engine.connect() as c:
            return {t: c.execute(text(f"SELECT count(*) FROM {t} WHERE facility_id = :f"), {"f": self.bank}).scalar() for t in TABLES}

    # -- preview ------------------------------------------------------------

    def test_the_preview_writes_nothing(self):
        before = self.counts()
        for name, data in (("h.csv", long_csv(30)), ("h.xlsx", xlsx([["Date"] + list(BLOOD), [str(days_ago(1))] + [5] * 8]))):
            status, body = self.preview(name, data)
            self.assertEqual(status, 200, body)
            self.assertGreater(body["row_counts"]["usable"], 0)
        self.assertEqual(self.counts(), before)

    def test_what_the_preview_says(self):
        status, body = self.preview("h.csv", b"Stock Date,Group,Qty\n" + f"{days_ago(2)},O pos,12\n{days_ago(1)},zz,3\n".encode())
        self.assertEqual(status, 200, body)
        self.assertEqual((body["layout"], body["row_counts"], body["ambiguous_dates"]), ("long", {"total": 2, "usable": 1, "skipped": 1}, False))
        self.assertEqual(body["mapping"], {"date": "Stock Date", "blood_type": "Group", "units": "Qty"})
        self.assertEqual(body["sample_rows"], [{"row": 2, "snapshot_date": str(days_ago(2)), "blood_type": "O+", "units": 12}])
        self.assertEqual(body["problems"][0]["row"], 3)

    def test_problems_are_capped_at_20_with_a_count_of_the_rest(self):
        rows = "\n".join(f"{days_ago(1)},O+,x" for _ in range(25))
        status, body = self.preview("h.csv", ("date,type,units\n" + rows).encode())
        self.assertEqual((len(body["problems"]), body["more_problems"], body["row_counts"]["skipped"]), (20, 5, 25))

    def test_the_sample_is_at_most_10_rows(self):
        self.assertEqual(len(self.preview("h.csv", long_csv(30))[1]["sample_rows"]), 10)

    def test_an_unusable_file_is_a_preview_with_no_layout_not_an_error(self):
        status, body = self.preview("h.csv", b"name,city\nx,y\n")
        self.assertEqual((status, body["layout"], body["row_counts"]["usable"]), (200, None, 0))
        self.assertIn("We could not find the columns", body["fatal"])

    # -- dates --------------------------------------------------------------

    def test_ambiguous_dates_are_blocked_until_a_choice_is_sent(self):
        data = b"date,type,units\n03/04/2026,O+,5\n05/06/2026,A+,6\n"
        before = self.counts()
        status, body = self.preview("h.csv", data)
        self.assertTrue(body["ambiguous_dates"])
        self.assertEqual(body["row_counts"]["usable"], 0)
        status, body = self.save("h.csv", data)
        self.assertEqual(status, 400)
        self.assertIn("day-first or month-first", body["detail"])
        self.assertEqual(self.counts(), before)  # not even an upload record
        _, dmy = self.preview("h.csv", data, order="dmy")
        _, mdy = self.preview("h.csv", data, order="mdy")
        self.assertEqual((dmy["row_counts"]["usable"], dmy["sample_rows"][0]["snapshot_date"]), (2, "2026-04-03"))
        self.assertEqual(mdy["sample_rows"][0]["snapshot_date"], "2026-03-04")
        status, saved = self.save("h.csv", data, order="mdy")
        self.assertEqual((status, saved["rows_processed"]), (200, 2))
        with engine.connect() as c:
            dates = c.execute(text("SELECT snapshot_date FROM inventory_snapshots WHERE facility_id = :f AND blood_type = 'O+'"), {"f": self.bank}).scalars().all()
        self.assertEqual([str(d) for d in dates], ["2026-03-04"])

    def test_a_choice_that_is_not_dmy_or_mdy_is_a_400(self):
        status, body = self.preview("h.csv", b"date,type,units\n", order="yes")
        self.assertEqual(status, 400)

    # -- the save -----------------------------------------------------------

    def test_the_save_writes_what_the_preview_counted(self):
        wide = xlsx([["Day"] + list(BLOOD)] + [[days_ago(d)] + [d + i for i in range(8)] for d in range(1, 41)])
        for name, data in (("long.csv", long_csv(120)), ("wide.xlsx", wide)):
            self.clear()
            status, p = self.preview(name, data)
            status, s = self.save(name, data)
            self.assertEqual(status, 200, s)
            self.assertEqual(s["rows_processed"], p["row_counts"]["usable"], name)
            self.assertEqual(len(s["errors"]), p["row_counts"]["skipped"], name)
            self.assertEqual(self.counts()["inventory_snapshots"], p["row_counts"]["usable"], name)  # no repeats in these files
            self.assertEqual(s["days_of_history"], 120 if name.endswith("csv") else 40)
            self.assertEqual(set(s), {"rows_processed", "errors", "days_of_history", "min_days_required"})

    def test_a_file_with_problems_saves_the_good_rows_and_lists_the_rest(self):
        data = f"date,type,units\n{days_ago(3)},O+,5\n{days_ago(2)},O+,-1\n{days_ago(1)},O+,2.5\n".encode()
        _, p = self.preview("h.csv", data)
        status, s = self.save("h.csv", data)
        self.assertEqual((s["rows_processed"], [e["row"] for e in s["errors"]]), (p["row_counts"]["usable"], [3, 4]))
        self.assertEqual(self.counts()["upload_history"], 1)

    def test_the_original_is_kept_for_a_csv_and_not_for_a_workbook(self):
        self.save("h.csv", long_csv(2))
        self.save("h.xlsx", xlsx([["Date", "Type", "Units"], [str(days_ago(1)), "O+", 3]]))
        with engine.connect() as c:
            rows = c.execute(text("SELECT filename, raw_content IS NOT NULL FROM upload_history WHERE facility_id = :f ORDER BY id"), {"f": self.bank}).all()
        self.assertEqual([tuple(r) for r in rows], [("h.csv", True), ("h.xlsx", False)])

    def test_the_save_clears_the_forecast_cache_and_logs_for_undo(self):
        with engine.begin() as c:
            c.execute(text("INSERT INTO facility_forecast_cache (facility_id, blood_type, forecast_date, forecast_units, lower_units, upper_units, "
                           "trained_through_date, model_order) VALUES (:f, 'O+', :d, 1, 0, 2, :d, 'x')"), {"f": self.bank, "d": TODAY})
        self.save("h.xlsx", xlsx([["Date", "Type", "Units"], [str(days_ago(1)), "O+", 3]]))
        after = self.counts()
        self.assertEqual((after["facility_forecast_cache"], after["inventory_snapshot_write_log"]), (0, 1))

    def test_undo_still_removes_a_new_format_upload(self):
        _, s = self.save("h.xlsx", xlsx([["Date"] + list(BLOOD), [str(days_ago(1))] + [5] * 8]))
        self.assertEqual(s["rows_processed"], 8)
        with engine.connect() as c:
            upload_id = c.execute(text("SELECT id FROM upload_history WHERE facility_id = :f"), {"f": self.bank}).scalar()
        status, raw = asyncio.run(asgi("POST", f"/upload-history/{upload_id}/undo", self.tokens["bank"]))
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["removed_count"], 8)
        self.assertEqual(self.counts()["inventory_snapshots"], 0)

    def test_a_workbook_with_two_equal_keys_keeps_the_last(self):
        data = xlsx([["Date", "Type", "Units"], [str(days_ago(1)), "O+", 3], [str(days_ago(1)), "o pos", 9]])
        _, p = self.preview("h.xlsx", data)
        self.assertEqual((p["row_counts"]["usable"], p["repeated"]), (2, 1))
        self.save("h.xlsx", data)
        with engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT units FROM inventory_snapshots WHERE facility_id = :f"), {"f": self.bank}).scalars().all(), [9])

    # -- who and what -------------------------------------------------------

    def test_a_hospital_gets_403_on_both(self):
        before = self.counts()
        for path in ("/forecast/historical-upload/preview", "/forecast/historical-upload"):
            status, body = self.post(path, "h.csv", long_csv(2), who="hospital")
            self.assertEqual(status, 403, path)
        self.assertEqual(self.counts(), before)

    def test_the_old_and_the_forged_files(self):
        for name, data, word in (("h.xls", b"x", "save it as .xlsx or .csv"), ("h.xlsm", b"x", "save it as .xlsx or .csv"),
                                 ("h.xlsx", b"%PDF-1.4", "not a real .xlsx"), ("h.csv", b"%PDF-1.4", "not a plain-text CSV"),
                                 ("h.xlsx", xlsx([["a"]])[:200], "not a real .xlsx"), ("h.pdf", b"%PDF", ".csv or .xlsx")):
            for path in ("/forecast/historical-upload/preview", "/forecast/historical-upload"):
                status, body = self.post(path, name, data)
                self.assertEqual(status, 400, (name, path))
                self.assertIn(word, body["detail"], name)
        self.assertEqual(self.counts()["upload_history"], 0)

    def test_an_oversize_file_is_413_on_both(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "1000"}):
            for path in ("/forecast/historical-upload/preview", "/forecast/historical-upload"):
                for name in ("h.csv", "h.xlsx"):
                    status, body = self.post(path, name, b"x" * 5000)
                    self.assertEqual(status, 413, (path, name))

    def test_too_many_figures_is_a_400_with_a_clear_message(self):
        import history_import
        with mock.patch.object(history_import, "MAX_ENTRIES", 10):
            for path in ("/forecast/historical-upload/preview", "/forecast/historical-upload"):
                status, body = self.post(path, "h.csv", long_csv(2) * 1 + b"\n" + b"\n".join([b"2026-01-01,O+,1"] * 20))
                self.assertEqual(status, 400, path)
                self.assertIn("more than 10 figures", body["detail"])
        self.assertEqual(self.counts()["upload_history"], 0)

    def test_an_entity_expansion_workbook_is_a_400_on_both(self):
        from tests.test_history_import import laughs_sheet, rewrite_member
        raw = rewrite_member(xlsx([["Date", "Type", "Units"]]), "xl/worksheets/sheet1.xml", laughs_sheet())
        for path in ("/forecast/historical-upload/preview", "/forecast/historical-upload"):
            status, body = self.post(path, "h.xlsx", raw)
            self.assertEqual(status, 400, path)
            self.assertIn("unsafe", body["detail"])
        self.assertEqual(self.counts()["upload_history"], 0)
