"""The historical stock file reader (history_import.py) and the file checks in front of it (upload_limits.read_table_upload).
Pure: no database. Run through pytest (conftest.py), like every test here."""
import datetime as dt
import io
import unittest
import zipfile
from unittest import mock

from fastapi import HTTPException
from openpyxl import Workbook
from starlette.datastructures import UploadFile

import history_import as h
import upload_limits as ul

TODAY = dt.date(2026, 11, 14)
ALL = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")


def parse(text, order=None):
    return h.parse_history_upload("csv", text, TODAY, order)


def xlsx_bytes(*sheets):
    """Each sheet is a list of rows; the first is the active (first) sheet."""
    wb = Workbook()
    wb.remove(wb.active)
    for i, rows in enumerate(sheets, start=1):
        ws = wb.create_sheet(f"Sheet{i}" if i > 1 else "Stock")
        for r in rows:
            ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def usable(p):
    return [(r["snapshot_date"].isoformat(), r["blood_type"], r["units"]) for r in p.rows]


class StrictFormatTests(unittest.TestCase):
    def test_the_original_three_column_csv_reads_as_before(self):
        p = parse("snapshot_date,blood_type,units\n2026-01-15,O+,120\n2026-01-16,AB-,0\n")
        self.assertEqual(usable(p), [("2026-01-15", "O+", 120), ("2026-01-16", "AB-", 0)])
        self.assertEqual((p.layout, p.errors, p.ambiguous, p.rows[0]["row"]), ("long", [], False, 2))

    def test_today_and_the_future_are_refused(self):
        p = parse("snapshot_date,blood_type,units\n2026-11-13,O+,1\n2026-11-14,O+,1\n2026-12-01,O+,1\n")
        self.assertEqual(len(p.rows), 1)
        self.assertEqual([e["row"] for e in p.problems], [3, 4])
        self.assertIn("must be before today", p.problems[0]["reason"])

    def test_a_header_only_file_is_a_file_with_nothing_to_save(self):
        p = parse("snapshot_date,blood_type,units\n")
        self.assertEqual((p.layout, p.rows, p.problems, p.fatal), ("long", [], [], None))

    def test_an_empty_file_and_a_file_with_no_usable_header(self):
        self.assertEqual(parse("").errors, [{"row": 0, "reason": "The file is empty."}])
        p = parse("name,city\nx,y\n")
        self.assertIsNone(p.layout)
        self.assertIn("name, city", p.fatal)
        self.assertEqual(p.preview()["row_counts"]["usable"], 0)


class LongLayoutTests(unittest.TestCase):
    def test_synonym_headers_in_any_order_with_extra_columns(self):
        p = parse("﻿ Notes ,QTY,Blood  Group,As Of\nok,10,o pos,2026-01-01\n,7,AB negative,2026-01-02\n")
        self.assertEqual(p.mapping, {"date": "As Of", "blood_type": "Blood  Group", "units": "QTY"})
        self.assertEqual(usable(p), [("2026-01-01", "O+", 10), ("2026-01-02", "AB-", 7)])

    def test_each_synonym_is_recognised(self):
        for d in ("date", "snapshot_date", "day", "as of", "stock date"):
            for t in ("blood_type", "blood group", "bloodtype", "group", "type", "abo"):
                for u in ("units", "qty", "quantity", "stock", "count", "on hand", "balance"):
                    p = parse(f"{d},{t},{u}\n2026-01-01,A+,3\n")
                    self.assertEqual(len(p.rows), 1, (d, t, u))

    def test_header_rule_is_whole_word_not_substring(self):
        # "stock date" is the date column; it must not also count as the "stock" (units) column
        p = parse("stock date,blood group,count\n2026-01-01,A+,3\n")
        self.assertEqual(p.mapping["units"], "count")

    def test_two_columns_for_one_role_is_put_to_the_user_not_guessed(self):
        # was: refused with "More than one column could be the units column". Now the candidates come back and nothing is read.
        p = parse("date,blood_type,units,stock\n2026-01-01,A+,3,4\n")
        self.assertIsNone(p.fatal)
        self.assertEqual(p.rows, [])
        self.assertEqual(p.column_choices, {"units": [{"name": "units", "samples": ["3"]}, {"name": "stock", "samples": ["4"]}]})

    def test_title_rows_above_the_header(self):
        p = parse("Northside stock log\n\nDate,Type,Units\n2026-01-01,B+,5\n")
        self.assertEqual((p.header_row, p.rows[0]["row"]), (3, 4))

    def test_semicolon_and_tab_separated_files(self):
        for sep in (";", "\t"):
            self.assertEqual(len(parse(sep.join(["date", "type", "units"]) + "\n" + sep.join(["2026-01-01", "O-", "9"]) + "\n").rows), 1)


class WideLayoutTests(unittest.TestCase):
    def test_one_column_per_blood_type_becomes_long_rows(self):
        head = "Date," + ",".join(ALL)
        p = parse(head + "\n2026-01-01," + ",".join(str(i) for i in range(8)) + "\n2026-01-02," + ",".join("5" * 8) + "\n")
        self.assertEqual(p.layout, "wide")
        self.assertEqual(len(p.rows), 16)
        self.assertEqual(usable(p)[:3], [("2026-01-01", "A+", 0), ("2026-01-01", "A-", 1), ("2026-01-01", "B+", 2)])

    def test_headers_written_out_and_a_subset_of_types(self):
        p = parse("day,O pos,O Negative\n2026-01-01,4,5\n")
        self.assertEqual(usable(p), [("2026-01-01", "O+", 4), ("2026-01-01", "O-", 5)])
        self.assertEqual(p.mapping["blood_types"], {"O pos": "O+", "O Negative": "O-"})

    def test_a_bad_cell_skips_only_that_figure_and_a_bad_date_skips_the_row(self):
        p = parse("Date,A+,A-\n2026-01-01,5,\n2026-01-02,x,6\nnot a date,1,1\n")
        self.assertEqual(usable(p), [("2026-01-02", "A-", 6), ("2026-01-01", "A+", 5)][::-1])
        self.assertEqual([e["row"] for e in p.problems], [2, 3, 4])
        self.assertTrue(p.problems[0]["reason"].startswith("A-:"))
        self.assertEqual(p.preview()["row_counts"], {"total": 5, "usable": 2, "skipped": 3})

    def test_two_columns_for_the_same_blood_type_is_refused(self):
        self.assertIn("Two columns are both O+", parse("Date,O+,O pos\n2026-01-01,1,2\n").fatal)

    def test_a_long_and_a_wide_layout_at_once_is_refused(self):
        self.assertIn("two layouts", parse("Date,Type,Units,O+\n2026-01-01,A+,1,2\n").fatal)


class BloodTypeSpellingTests(unittest.TestCase):
    def test_every_spelling(self):
        cases = {"O+": ["O+", "o+", "O pos", "O POS", "o positive", "Opositive", "O +", "O plus", "O+ve", "O (pos)"],
                 "O-": ["O-", "o-", "O neg", "O negative", "Onegative", "O NEG", "O -ve", "O−", "O–", "O minus"],
                 "AB+": ["AB+", "ab pos", "AB Positive", "ABpositive"], "AB-": ["AB-", "ab neg", "AB Negative"],
                 "A+": ["A+", "a pos", "A positive"], "A-": ["A-", "A neg"], "B+": ["B+", "b POS"], "B-": ["B-", "B negative"]}
        for want, spellings in cases.items():
            for s in spellings:
                self.assertEqual(h.blood_type(s), want, s)

    def test_anything_else_is_not_guessed(self):
        for s in ("", "O", "AB", "ZZ", "0+", "O?", "C+", "pos", "A+B", "Rh+", None, 5):
            self.assertIsNone(h.blood_type(s), repr(s))

    def test_an_unrecognised_value_is_a_skipped_row_with_a_reason(self):
        p = parse("date,type,units\n2026-01-01,ZZ,5\n2026-01-01,,5\n2026-01-01,O+,5\n")
        self.assertEqual(len(p.rows), 1)
        self.assertIn("'ZZ' is not one we recognise", p.problems[0]["reason"])
        self.assertIn("blank", p.problems[1]["reason"])

    def test_the_canonical_set_is_the_servers(self):
        import main
        self.assertEqual(set(h.BLOOD_TYPES), main.VALID_BLOOD_TYPES)


class DateTests(unittest.TestCase):
    def one(self, value, order=None):
        p = parse(f'date,type,units\n"{value}",O+,1\n', order)  # quoted: "Nov 10, 2026" has a comma
        return p, (p.rows[0]["snapshot_date"] if p.rows else None)

    def test_iso_and_year_first_and_with_a_time(self):
        for v in ("2026-11-10", "2026-1-5", "2026/11/10", "2026.11.10", "2026-11-10 00:00:00", "2026-11-10T00:00:00"):
            p, d = self.one(v)
            self.assertEqual(d.year, 2026, v)
            self.assertFalse(p.ambiguous)

    def test_day_first_is_proved_by_a_day_over_12(self):
        p = parse("date,type,units\n13/02/2026,O+,1\n03/04/2026,O+,2\n")
        self.assertEqual((p.date_order, [r["snapshot_date"] for r in p.rows]), ("dmy", [dt.date(2026, 2, 13), dt.date(2026, 4, 3)]))

    def test_month_first_is_proved_by_a_day_over_12(self):
        p = parse("date,type,units\n02/13/2026,O+,1\n03/04/2026,O+,2\n")
        self.assertEqual((p.date_order, [r["snapshot_date"] for r in p.rows]), ("mdy", [dt.date(2026, 2, 13), dt.date(2026, 3, 4)]))

    def test_separators(self):
        for sep in "/.-":
            p, d = self.one(f"25{sep}12{sep}2025")
            self.assertEqual(d, dt.date(2025, 12, 25))

    def test_written_months(self):
        for v in ("10 Nov 2026", "10 November 2026", "Nov 10, 2026", "November 10 2026", "10-Nov-2026", "10th Nov 2026", "Nov. 10, 2026"):
            p, d = self.one(v)
            self.assertEqual(d, dt.date(2026, 11, 10), v)
            self.assertFalse(p.ambiguous)
        self.assertEqual(self.one("5 Sept 2026")[1], dt.date(2026, 9, 5))

    def test_excel_dates(self):
        p = h.parse_history_upload("xlsx", xlsx_bytes([["Date", "Type", "Units"], [dt.datetime(2026, 3, 4), "O+", 5]]), TODAY)
        self.assertEqual(usable(p), [("2026-03-04", "O+", 5)])
        self.assertFalse(p.ambiguous)  # a real Excel date has no day/month doubt

    def test_a_date_that_can_be_read_either_way_is_not_guessed(self):
        p = parse("date,type,units\n03/04/2026,O+,1\n05/06/2026,O+,2\n")
        v = p.preview()
        self.assertTrue(v["ambiguous_dates"])
        self.assertEqual(v["ambiguous_example"], {"text": "03/04/2026", "day_first": "3 April 2026", "month_first": "4 March 2026"})
        self.assertEqual((v["row_counts"]["usable"], v["sample_rows"]), (0, []))  # nothing usable until the user chooses

    def test_the_choice_decides(self):
        text = "date,type,units\n03/04/2026,O+,1\n"
        self.assertEqual(parse(text, "dmy").rows[0]["snapshot_date"], dt.date(2026, 4, 3))
        self.assertEqual(parse(text, "mdy").rows[0]["snapshot_date"], dt.date(2026, 3, 4))
        self.assertFalse(parse(text, "dmy").ambiguous)

    def test_under_a_choice_a_row_that_cannot_be_read_that_way_is_skipped(self):
        p = parse("date,type,units\n03/04/2026,O+,1\n25/04/2026,O+,1\n", "mdy")
        self.assertEqual(len(p.rows), 1)
        self.assertIn("25/04/2026 cannot be read as month/day/year", p.problems[0]["reason"])

    def test_a_file_that_proves_both_orders_must_be_chosen(self):
        self.assertTrue(parse("date,type,units\n13/04/2026,O+,1\n04/13/2026,O+,1\n").ambiguous)

    def test_a_date_that_reads_the_same_either_way_is_not_ambiguous(self):
        p = parse("date,type,units\n03/03/2026,O+,1\n")
        self.assertFalse(p.ambiguous)
        self.assertEqual(len(p.rows), 1)

    def test_unreadable_dates(self):
        for v in ("31/02/2026", "2026-02-30", "soon", "10/11/26", "46336", "13/13/2026"):
            p, d = self.one(v)
            self.assertIsNone(d, v)
            self.assertEqual(len(p.problems), 1, v)

    def test_a_choice_that_is_not_dmy_or_mdy_is_ignored_by_the_reader(self):
        self.assertTrue(parse("date,type,units\n03/04/2026,O+,1\n", "yes").ambiguous)  # the endpoint rejects it with a 400


class UnitsTests(unittest.TestCase):
    def units(self, v):
        p = parse(f'date,type,units\n2026-01-01,O+,"{v}"\n')
        return (p.rows[0]["units"], None) if p.rows else (None, p.problems[0]["reason"])

    def test_whole_numbers(self):
        self.assertEqual(self.units("0")[0], 0)
        self.assertEqual(self.units("120")[0], 120)
        self.assertEqual(self.units("1,200")[0], 1200)
        self.assertEqual(self.units("12.0")[0], 12)  # exact, nothing rounded

    def test_each_kind_of_bad_value_is_skipped_with_its_own_reason(self):
        self.assertIn("negative", self.units("-5")[1])
        self.assertIn("not a whole number", self.units("12.5")[1])
        self.assertIn("not a whole number", self.units("1.200")[1])   # not a thousands separator
        self.assertIn("blank", self.units("")[1])
        self.assertIn("blank", self.units("  ")[1])
        self.assertIn("not a number", self.units("abc")[1])
        self.assertIn("not a number", self.units("1,5")[1])
        self.assertIn("not a number", self.units("#N/A")[1])
        self.assertIn("too large", self.units("99999999999")[1])

    def test_excel_numbers(self):
        p = h.parse_history_upload("xlsx", xlsx_bytes([["Date", "Type", "Units"], ["2026-01-01", "O+", 12.0], ["2026-01-02", "O+", 12.5],
                                                      ["2026-01-03", "O+", -1], ["2026-01-04", "O+", None], ["2026-01-05", "O+", True], ["2026-01-06", "O+", dt.datetime(2026, 1, 6)]]), TODAY)
        self.assertEqual(usable(p), [("2026-01-01", "O+", 12)])
        self.assertEqual(len(p.problems), 5)  # a date or a yes/no in a units cell is a skipped row, not a crash


class LimitsTests(unittest.TestCase):
    def test_too_many_figures_stops_with_a_clear_message(self):
        with mock.patch.object(h, "MAX_ENTRIES", 5):
            with self.assertRaises(h.HistoryFileError) as cm:
                parse("date,type,units\n" + "2026-01-01,O+,1\n" * 6)
        self.assertIn("more than 5 figures", str(cm.exception))

    def test_a_sheet_that_runs_on_over_empty_rows_is_cut_off(self):
        rows = [["Date", "Type", "Units"], ["2026-01-01", "O+", 1]] + [[None, None, None]] * 1500 + [["2026-01-02", "O+", 1]]
        self.assertEqual(len(h.parse_history_upload("xlsx", xlsx_bytes(rows), TODAY).rows), 1)

    def test_blank_rows_between_data_are_ignored(self):
        p = parse("date,type,units\n2026-01-01,O+,1\n,,\n\n2026-01-02,O+,1\n")
        self.assertEqual((len(p.rows), p.problems), (2, []))


class XlsxTests(unittest.TestCase):
    def test_long_and_wide_workbooks(self):
        long = h.parse_history_upload("xlsx", xlsx_bytes([["Snapshot Date", "Blood Type", "Units"], ["2026-01-01", "O pos", 7]]), TODAY)
        wide = h.parse_history_upload("xlsx", xlsx_bytes([["Date"] + list(ALL), [dt.date(2026, 1, 1)] + list(range(8))]), TODAY)
        self.assertEqual((long.layout, usable(long)), ("long", [("2026-01-01", "O+", 7)]))
        self.assertEqual((wide.layout, len(wide.rows)), ("wide", 8))
        self.assertEqual((long.sheet, long.other_sheets), ("Stock", []))

    def test_only_the_first_sheet_is_read_and_the_others_are_named(self):
        p = h.parse_history_upload("xlsx", xlsx_bytes([["Date", "Type", "Units"], ["2026-01-01", "O+", 1]],
                                                      [["Date", "Type", "Units"], ["2026-02-02", "A+", 2]], []), TODAY)
        self.assertEqual((p.sheet, p.other_sheets, len(p.rows)), ("Stock", ["Sheet2"], 1))

    def test_a_file_that_is_not_a_workbook_inside_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", "<nope/>")
        with self.assertRaises(h.HistoryFileError):
            h.parse_history_upload("xlsx", buf.getvalue(), TODAY)


def upload(name, data: bytes):
    return UploadFile(file=io.BytesIO(data), filename=name)


class FileCheckTests(unittest.TestCase):
    def refused(self, name, data, status=400):
        with self.assertRaises(HTTPException) as cm:
            ul.read_table_upload(upload(name, data))
        self.assertEqual(cm.exception.status_code, status, cm.exception.detail)
        return cm.exception.detail

    def test_csv_and_xlsx_are_accepted(self):
        self.assertEqual(ul.read_table_upload(upload("a.CSV", b"\xef\xbb\xbfdate,type,units\n"))[0], "csv")
        kind, content = ul.read_table_upload(upload("a.xlsx", xlsx_bytes([["Date", "Type", "Units"]])))
        self.assertEqual((kind, content[:2]), ("xlsx", b"PK"))

    def test_old_excel_and_macro_files_get_a_plain_message(self):
        for name in ("a.xls", "a.xlsm", "a.xlsb", "A.XLS"):
            self.assertIn("save it as .xlsx or .csv", self.refused(name, b"x"))

    def test_other_extensions(self):
        self.assertIn(".csv or .xlsx", self.refused("a.txt", b"x"))
        self.assertIn(".csv or .xlsx", self.refused("a", b"x"))

    def test_a_renamed_zip_pdf_or_old_xls_is_not_a_workbook(self):
        plain_zip = io.BytesIO()
        with zipfile.ZipFile(plain_zip, "w") as z:
            z.writestr("notes.txt", "hello")
        self.assertIn("not an Excel workbook", self.refused("a.xlsx", plain_zip.getvalue()))
        self.assertIn("not a real .xlsx", self.refused("a.xlsx", b"%PDF-1.4 ..."))
        self.assertIn("not a real .xlsx", self.refused("a.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100))
        self.assertIn("not a real .xlsx", self.refused("a.xlsx", b"PK\x03\x04 truncated"))
        self.assertIn("not a real .xlsx", self.refused("a.xlsx", b"just text"))

    def test_the_zip_signature_stays_refused_in_a_csv(self):
        for name in ("a.csv",):
            self.assertIn("not a plain-text CSV", self.refused(name, xlsx_bytes([["a"]])))
        self.assertIn("not a plain-text CSV", self.refused("a.csv", b"%PDF-1.4"))

    def test_a_workbook_with_macros_is_refused(self):
        buf = io.BytesIO(xlsx_bytes([["a"]]))
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("xl/vbaProject.bin", b"x")
        self.assertIn("macros", self.refused("a.xlsx", buf.getvalue()))

    def test_a_zip_bomb_is_refused_before_it_is_opened(self):
        buf = io.BytesIO(xlsx_bytes([["a"]]))
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/worksheets/sheet9.xml", b"0" * (60 * 1024 * 1024))  # 60 MB of zeros: a few dozen KB zipped
        self.assertLess(len(buf.getvalue()), 200_000)
        self.assertIn("too large once opened", self.refused("a.xlsx", buf.getvalue()))

    def test_an_oversize_file_is_413(self):
        with mock.patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "100"}):
            self.refused("a.csv", b"x" * 101, 413)
            self.refused("a.xlsx", b"x" * 101, 413)


# ── hostile workbooks ────────────────────────────────────────────────────────

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
LAUGHS = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">' +
          "".join(f'<!ENTITY lol{i} "{("&lol%s;" % (i - 1 if i > 1 else "")) * 10}">' for i in range(1, 10)) +
          "]>")


def laughs_sheet():
    return (LAUGHS + f'<worksheet xmlns="{SHEET_NS}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&lol9;</t></is></c></row>'
            "</sheetData></worksheet>").encode()


def rewrite_member(raw: bytes, name: str, new: bytes, declared=None) -> bytes:
    """The workbook `raw` with part `name` replaced by `new`; with `declared`, its headers (local and central) are made to claim that
    uncompressed size instead of the real one, which is how a zip bomb hides from a check of the table of contents."""
    src, out = zipfile.ZipFile(io.BytesIO(raw)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info.filename, new if info.filename == name else src.read(info.filename))
    data = bytearray(out.getvalue())
    if declared is not None:
        info = next(i for i in zipfile.ZipFile(io.BytesIO(bytes(data))).infolist() if i.filename == name)
        struct_pack(data, info.header_offset + 22, declared)                  # local header: uncompressed size
        pos = 0
        while (pos := bytes(data).find(b"PK\x01\x02", pos)) >= 0:
            if bytes(data[pos + 46:pos + 46 + len(name)]) == name.encode():
                struct_pack(data, pos + 24, declared)                          # central directory: uncompressed size
            pos += 4
    return bytes(data)


def struct_pack(buf, offset, value):
    import struct
    struct.pack_into("<I", buf, offset, value)


class HostileWorkbookTests(unittest.TestCase):
    def test_openpyxl_really_reads_xml_through_defusedxml(self):
        import openpyxl.xml
        import openpyxl.xml.functions as fn
        self.assertTrue(openpyxl.xml.DEFUSEDXML)
        self.assertTrue(fn.iterparse.__module__.startswith("defusedxml"), fn.iterparse.__module__)

    def test_an_entity_expansion_payload_in_a_sheet_is_refused_plainly(self):
        import tracemalloc
        raw = rewrite_member(xlsx_bytes([["Date", "Type", "Units"]]), "xl/worksheets/sheet1.xml", laughs_sheet())
        ul.check_xlsx(raw)  # the zip itself is small and well formed: the refusal must come from the XML reader
        tracemalloc.start()
        with self.assertRaises(h.HistoryFileError) as cm:
            h.parse_history_upload("xlsx", raw, TODAY)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertIn("unsafe", str(cm.exception))
        self.assertLess(peak, 20 * 1024 * 1024)  # nothing was expanded: the payload would be ~1 GB

    def test_an_entity_payload_in_the_workbook_part_is_refused_too(self):
        evil = (LAUGHS + f'<workbook xmlns="{SHEET_NS}"><sheets><sheet name="&lol9;" sheetId="1"/></sheets></workbook>').encode()
        raw = rewrite_member(xlsx_bytes([["Date", "Type", "Units"]]), "xl/workbook.xml", evil)
        with self.assertRaises(h.HistoryFileError) as cm:
            h.parse_history_upload("xlsx", raw, TODAY)
        self.assertIn("unsafe", str(cm.exception))

    def test_a_part_whose_header_hides_its_real_size_is_cut_off_not_read(self):
        real = 60 * 1024 * 1024
        sheet = f'<?xml version="1.0"?><worksheet xmlns="{SHEET_NS}"><sheetData>'.encode() + b"<row/>" * (real // 6)
        raw = rewrite_member(xlsx_bytes([["Date", "Type", "Units"]]), "xl/worksheets/sheet1.xml", sheet, declared=1000)
        self.assertLess(len(raw), 500_000)
        ul.check_xlsx(raw)  # the table of contents says 1000 bytes: this check cannot see the lie
        read = [0]
        real_read = zipfile.ZipExtFile.read

        def counting(self, n=-1):
            data = real_read(self, n)
            read[0] += len(data)
            return data

        with mock.patch.object(zipfile.ZipExtFile, "read", counting):
            with self.assertRaises(h.HistoryFileError):
                h.parse_history_upload("xlsx", raw, TODAY)
        self.assertLess(read[0], 1_000_000)  # stopped near the declared 1000 bytes, not 60 MB


# ── more than one column could fill a role: the user picks ───────────────────

NORTHSIDE_HEADER = "facility_code,Snapshot_Date,component, Blood_Type ,total_units,units_available,storage_location,recorded_by,remarks,last_updated,UNITS"


def northside_csv(days=3):
    """Shaped like upload 928's file: units_available is one number and UNITS is that number + 5."""
    lines = [NORTHSIDE_HEADER]
    for d in range(days):
        for i, t in enumerate(ALL):
            ua = 10 + d * 3 + i
            lines.append(f"NCB-05,2026-01-{d + 1:02d},Packed RBC,{t},{ua + 100},{ua},Bay 1,avillanueva,,2026-01-01T18:05:00Z,{ua + 5}")
    return "\r\n".join(lines) + "\r\n"


class ColumnChoiceTests(unittest.TestCase):
    def test_a_northside_shaped_file_returns_both_candidates_with_their_first_values_and_reads_nothing(self):
        p = parse(northside_csv())
        v = p.preview()
        self.assertTrue(v["needs_column_choice"])
        self.assertEqual(list(v["column_choices"]), ["units"])  # the date and blood type columns are unambiguous
        self.assertEqual(v["column_choices"]["units"], [{"name": "units_available", "samples": ["10", "11", "12"]},
                                                        {"name": "UNITS", "samples": ["15", "16", "17"]}])
        self.assertEqual((v["row_counts"]["usable"], v["sample_rows"], v["fatal"], v["layout"]), (0, [], None, "long"))

    def test_choosing_units_reads_the_units_column_and_ignores_the_other(self):
        p = parse_cols(northside_csv(), {"units": "UNITS"})
        self.assertEqual(p.column_choices, {})
        self.assertEqual(p.mapping, {"date": "Snapshot_Date", "blood_type": "Blood_Type", "units": "UNITS"})
        self.assertEqual([r["units"] for r in p.rows][:3], [15, 16, 17])
        self.assertEqual(len(p.rows), 24)

    def test_choosing_units_available_reads_those(self):
        p = parse_cols(northside_csv(), {"units": "units_available"})
        self.assertEqual([r["units"] for r in p.rows][:3], [10, 11, 12])

    def test_the_same_choice_gives_the_same_rows_every_time(self):
        a, b = parse_cols(northside_csv(), {"units": "UNITS"}), parse_cols(northside_csv(), {"units": "UNITS"})
        self.assertEqual(a.rows, b.rows)

    def test_a_choice_that_is_not_a_header_is_refused(self):
        for cols in ({"units": "nope"}, {"units": "units"}, {"date": "Date"}, {"type": "Blood Type"}):  # "units" is not spelled that way here
            with self.assertRaises(h.HistoryFileError) as cm:
                parse_cols(northside_csv(), cols)
            self.assertIn("is not in this file", str(cm.exception))

    def test_a_date_column_and_a_blood_type_column_can_be_ambiguous_too(self):
        p = parse("Date,Stock date,blood_type,Group,units\n2026-01-01,2026-02-01,O+,A+,5\n")
        self.assertEqual(sorted(p.column_choices), ["date", "type"])
        self.assertEqual(p.column_choices["date"][1], {"name": "Stock date", "samples": ["2026-02-01"]})
        done = parse_cols("Date,Stock date,blood_type,Group,units\n2026-01-01,2026-02-01,O+,A+,5\n", {"date": "Stock date", "type": "Group"})
        self.assertEqual(usable(done), [("2026-02-01", "A+", 5)])

    def test_one_answered_role_does_not_hide_the_others(self):
        p = parse_cols("Date,Stock date,blood_type,Group,units\n2026-01-01,2026-02-01,O+,A+,5\n", {"date": "Date"})
        self.assertEqual(list(p.column_choices), ["type"])
        self.assertEqual(p.rows, [])

    def test_a_wide_files_date_column_can_be_chosen(self):
        p = parse("Date,As of,O+\n2026-01-01,2026-02-01,5\n")
        self.assertEqual(list(p.column_choices), ["date"])
        self.assertEqual(usable(parse_cols("Date,As of,O+\n2026-01-01,2026-02-01,5\n", {"date": "As of"})), [("2026-02-01", "O+", 5)])

    def test_a_choice_for_a_role_with_one_candidate_is_used_when_it_is_a_real_header(self):
        p = parse_cols("date,type,units,notes\n2026-01-01,O+,5,9\n", {"units": "notes"})
        self.assertEqual(usable(p), [("2026-01-01", "O+", 9)])

    def test_a_choice_when_nothing_is_ambiguous_changes_nothing(self):
        text = "date,type,units\n2026-01-01,O+,5\n"
        self.assertEqual(usable(parse_cols(text, {"units": "units"})), usable(parse(text)))

    def test_samples_skip_blanks_stop_at_three_and_read_excel_dates(self):
        p = parse("date,type,units,stock\n2026-01-01,O+,,\n2026-01-02,O+,1,\n2026-01-03,O+,2,7\n2026-01-04,O+,3,8\n2026-01-05,O+,4,9\n2026-01-06,O+,5,10\n")
        self.assertEqual([s["samples"] for s in p.column_choices["units"]], [["1", "2", "3"], ["7", "8", "9"]])
        wb = xlsx_bytes([["Date", "Stock date", "Type", "Units"], [dt.datetime(2026, 1, 1), dt.datetime(2026, 2, 1), "O+", 5]])
        self.assertEqual(h.parse_history_upload("xlsx", wb, TODAY).column_choices["date"][1]["samples"], ["2026-02-01"])


def parse_cols(text, columns):
    return h.parse_history_upload("csv", text, TODAY, None, columns)


# ── one role has no recognised header: the same question, not a dead end ──────

VAULT = "Stock date,Blood group,Units counted in the vault,Notes\n2026-01-01,O+,5,fine\n2026-01-02,A+,6,\n2026-01-03,B-,7,low\n"


class MissingRoleTests(unittest.TestCase):
    def test_the_exact_header_from_the_report_is_recognised_through_the_synonym(self):
        p = parse("Stock date,Blood group,Qty on hand\n2026-01-01,O+,5\n")
        self.assertEqual((p.fatal, p.column_choices, p.column_missing), (None, {}, []))
        self.assertEqual(p.mapping, {"date": "Stock date", "blood_type": "Blood group", "units": "Qty on hand"})
        self.assertEqual(usable(p), [("2026-01-01", "O+", 5)])
        xl = h.parse_history_upload("xlsx", xlsx_bytes([["Stock date", "Blood group", "Qty on hand"], [dt.datetime(2026, 1, 1), "O+", 5]]), TODAY)
        self.assertEqual(usable(xl), [("2026-01-01", "O+", 5)])

    def test_every_added_synonym_is_recognised_whole(self):
        for u in ("qty on hand", "quantity on hand", "qty available", "stock level", "Stock Levels", "in stock", "current stock", "units in stock",
                  "number of units", "quantity available", "qty in stock", "stock quantity", "stock count", "available units"):
            p = parse(f"date,type,{u}\n2026-01-01,O+,5\n")
            self.assertEqual((p.column_choices, usable(p)), ({}, [("2026-01-01", "O+", 5)]), u)
        for d in ("date recorded", "recorded date", "date of record", "reporting date", "date reported", "count date", "date of count", "date counted"):
            p = parse(f"{d},type,units\n2026-01-01,O+,5\n")
            self.assertEqual((p.column_choices, usable(p)), ({}, [("2026-01-01", "O+", 5)]), d)

    def test_synonyms_still_match_whole_headers_only(self):
        # "stock level notes" and "units counted" contain synonyms but are not one: the units role stays missing
        for u in ("stock level notes", "units counted", "instock items"):
            p = parse(f"date,type,{u}\n2026-01-01,O+,5\n")
            self.assertEqual((list(p.column_choices), p.column_missing), (["units"], ["units"]), u)

    def test_a_missing_units_column_is_asked_with_every_free_column_and_its_first_three_values(self):
        p = parse(VAULT)
        self.assertEqual((p.fatal, p.rows, p.column_missing), (None, [], ["units"]))
        self.assertEqual(p.column_choices, {"units": [{"name": "Units counted in the vault", "samples": ["5", "6", "7"]},
                                                      {"name": "Notes", "samples": ["fine", "low"]}]})  # not Stock date or Blood group
        v = p.preview()
        self.assertEqual((v["needs_column_choice"], v["column_missing"], v["row_counts"]["usable"], v["layout"]), (True, ["units"], 0, "long"))

    def test_choosing_it_reads_that_column_through_the_usual_units_rules(self):
        p = parse_cols(VAULT, {"units": "Units counted in the vault"})
        self.assertEqual((p.column_choices, p.column_missing, p.fatal), ({}, [], None))
        self.assertEqual(p.mapping, {"date": "Stock date", "blood_type": "Blood group", "units": "Units counted in the vault"})
        self.assertEqual(usable(p), [("2026-01-01", "O+", 5), ("2026-01-02", "A+", 6), ("2026-01-03", "B-", 7)])
        bad = parse_cols("Stock date,Blood group,Counted\n2026-01-01,O+,-3\n2026-01-02,A+,2.5\n2026-01-03,B-,\n2026-01-04,O-,9\n", {"units": "Counted"})
        self.assertEqual(usable(bad), [("2026-01-04", "O-", 9)])
        self.assertEqual([e["row"] for e in bad.problems], [2, 3, 4])      # negative, decimal and blank are skipped with a reason, never a 0

    def test_a_chosen_column_most_of_whose_values_cannot_be_read_shows_in_the_skipped_list(self):
        p = parse_cols(VAULT, {"units": "Notes"})
        self.assertEqual((len(p.rows), len(p.problems)), (0, 3))
        v = p.preview()
        self.assertEqual((v["row_counts"], v["fatal"]), ({"total": 3, "usable": 0, "skipped": 3}, None))
        self.assertIn("fine", v["problems"][0]["reason"] + str(p.problems[0]))   # says what it could not read
        dates = parse_cols("Date,Blood group,Qty,Notes\nnope,O+,5,x\nnever,A+,6,y\n2026-01-01,B-,7,z\n", {"date": "Notes"})
        self.assertEqual((len(dates.rows), len(dates.problems)), (0, 3))

    def test_a_missing_date_or_blood_type_column_is_asked_the_same_way(self):
        p = parse("Blood group,Units,As recorded\nO+,5,2026-01-01\nA+,6,2026-01-02\n")
        self.assertEqual((p.column_missing, p.column_choices), (["date"], {"date": [{"name": "As recorded", "samples": ["2026-01-01", "2026-01-02"]}]}))
        self.assertEqual(usable(parse_cols("Blood group,Units,As recorded\nO+,5,2026-01-01\n", {"date": "As recorded"})), [("2026-01-01", "O+", 5)])
        t = parse("Date,Units,Kind\n2026-01-01,5,O pos\n")
        self.assertEqual((t.column_missing, list(t.column_choices)), (["type"], ["type"]))
        self.assertEqual(usable(parse_cols("Date,Units,Kind\n2026-01-01,5,O pos\n", {"type": "Kind"})), [("2026-01-01", "O+", 5)])

    def test_a_wide_file_with_no_date_header_asks_for_the_date(self):
        p = parse("Remarks,O+,O-\n2026-01-01,5,6\n")
        self.assertEqual((p.layout, p.column_missing, list(p.column_choices)), ("wide", ["date"], ["date"]))
        self.assertEqual(usable(parse_cols("Remarks,O+,O-\n2026-01-01,5,6\n", {"date": "Remarks"})), [("2026-01-01", "O+", 5), ("2026-01-01", "O-", 6)])

    def test_the_question_does_not_hide_another_role_that_also_has_several_candidates(self):
        p = parse("Date,As of,Blood group,Qty\n2026-01-01,2026-02-01,O+,5\n")
        self.assertEqual((sorted(p.column_choices), p.column_missing), (["date"], []))   # two date candidates: asked, but not 'missing'

    def test_a_column_already_used_for_another_role_cannot_be_chosen_for_the_missing_one(self):
        with self.assertRaises(h.HistoryFileError) as cm:
            parse_cols(VAULT, {"units": "Blood group"})
        self.assertIn("already used", str(cm.exception))
        with self.assertRaises(h.HistoryFileError):
            parse_cols(VAULT, {"units": "Nope"})

    def test_two_roles_missing_is_the_plain_error_naming_what_was_found(self):
        p = parse("Stock date,Counted,Notes\n2026-01-01,5,x\n")
        self.assertIsNone(p.layout)
        self.assertEqual(p.column_choices, {})
        self.assertTrue(p.fatal.startswith("We found a date column but could not tell which columns hold the blood type and the number of units."))
        self.assertIn("Stock date, Counted, Notes", p.fatal)
        self.assertEqual(parse("name,city\nx,y\n").fatal[:38], "We could not find the columns we need.")  # nothing found: as before

    def test_no_free_column_left_to_choose_is_the_plain_error(self):
        for text in ("Stock date,Blood group\n2026-01-01,O+\n", "Stock date,Blood group,\n2026-01-01,O+,5\n"):   # a blank header cannot be named
            p = parse(text)
            self.assertEqual((p.column_choices, p.rows), ({}, []))
            self.assertTrue(p.fatal.startswith("We found a date column and a blood type column but could not tell which column holds the number of units."), text)

    def test_a_column_with_nothing_in_it_is_not_offered_and_none_left_is_the_plain_error(self):
        p = parse("Stock date,Blood group,Empty,Counted\n2026-01-01,O+,,5\n")
        self.assertEqual([c["name"] for c in p.column_choices["units"]], ["Counted"])
        q = parse("Stock date,Blood group,Empty\n2026-01-01,O+,\n")
        self.assertEqual((q.column_choices, q.fatal is not None), ({}, True))
        self.assertIn("number of units", q.fatal)

    def test_the_same_choice_through_the_workbook_reader(self):
        wb = xlsx_bytes([["Stock date", "Blood group", "Units counted in the vault"], [dt.datetime(2026, 1, 1), "O+", 5]])
        self.assertEqual(h.parse_history_upload("xlsx", wb, TODAY).column_missing, ["units"])
        got = h.parse_history_upload("xlsx", wb, TODAY, None, {"units": "Units counted in the vault"})
        self.assertEqual(usable(got), [("2026-01-01", "O+", 5)])


# ── a missing optional library must never stop the server ────────────────────

class MissingLibrariesTests(unittest.TestCase):
    BLOCK = ("import sys\n"
             "for m in ('openpyxl', 'defusedxml', 'defusedxml.common'):\n"
             "    sys.modules[m] = None  # an import of it now raises ImportError, as if it were not installed\n")

    def test_the_server_starts_and_a_csv_still_reads_with_both_libraries_missing(self):
        import os
        import subprocess
        import sys
        code = self.BLOCK + (
            "import main, history_import as h\n"
            "from datetime import date\n"
            "assert 'openpyxl' not in sys.modules or sys.modules['openpyxl'] is None\n"
            "p = h.parse_history_upload('csv', 'date,type,units\\n2026-01-01,O+,5\\n', date(2026, 11, 15))\n"
            "assert len(p.rows) == 1 and p.rows[0]['units'] == 5\n"
            "try:\n"
            "    h.parse_history_upload('xlsx', b'PK', date(2026, 11, 15))\n"
            "except h.ExcelUnavailable as e:\n"
            "    print('MSG', e)\n"
            "print('STARTED')\n")
        server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", code], cwd=server_dir, env=dict(os.environ), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("STARTED", r.stdout)
        self.assertIn("MSG Excel files can't be read right now; save the file as .csv and try again.", r.stdout)

    def test_each_library_alone_missing_also_means_no_excel_not_a_crash(self):
        raw = xlsx_bytes([["Date", "Type", "Units"], ["2026-01-01", "O+", 1]])
        for missing in (("openpyxl",), ("defusedxml", "defusedxml.common")):
            with mock.patch.dict("sys.modules", {m: None for m in missing}):
                with self.assertRaises(h.ExcelUnavailable):
                    h.parse_history_upload("xlsx", raw, TODAY)
                self.assertEqual(len(parse("date,type,units\n2026-01-01,O+,5\n").rows), 1)
        self.assertEqual(len(h.parse_history_upload("xlsx", raw, TODAY).rows), 1)  # and it works again once they are back
