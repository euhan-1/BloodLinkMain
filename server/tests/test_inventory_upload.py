"""Unit tests for the Inventory CSV parsing logic (Step 8A).

Pure-function tests only — no DB, no HTTP. `_parse_inventory_csv` never touches
the database, so these run without a live Supabase connection. The DB-side
duplicate-DIN behavior (upsert-by-facility vs. cross-facility rejection) is
covered separately, by the live walkthrough, since it inherently needs a real
database to exercise the ON CONFLICT ... WHERE facility_id path.

Run from the server/ directory (so `import main` and dotenv discovery both
resolve the same way the running app does):

    .venv\\Scripts\\python.exe -m unittest tests.test_inventory_upload -v
"""

import datetime
import unittest

from main import _parse_inventory_csv

HEADER = "din,blood_type,component,location,volume_ml,collected_date,expires_date"


def csv_text(*data_lines: str) -> str:
    return "\n".join([HEADER, *data_lines])


class ParseInventoryCsvTests(unittest.TestCase):
    def test_valid_rows_parsed_with_correct_types(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["din"], "DIN-001")
        self.assertEqual(row["blood_type"], "O+")
        self.assertEqual(row["component"], "Packed RBC")
        self.assertEqual(row["location"], "Bay 1")
        self.assertEqual(row["volume_ml"], 280)
        self.assertIsInstance(row["volume_ml"], int)
        self.assertEqual(row["collected_date"], datetime.date(2026, 8, 1))
        self.assertEqual(row["expires_date"], datetime.date(2026, 9, 1))
        self.assertEqual(row["row"], 2)  # header is row 1

    def test_multiple_valid_rows_and_row_numbers(self):
        rows, errors = _parse_inventory_csv(
            csv_text(
                "DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01",
                "DIN-002,A-,Plasma,Bay 2,250,2026-08-02,2026-10-02",
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual([r["row"] for r in rows], [2, 3])

    def test_empty_file(self):
        rows, errors = _parse_inventory_csv("")
        self.assertEqual(rows, [])
        self.assertEqual(errors, [{"row": 0, "reason": "empty file"}])

    def test_missing_required_columns(self):
        rows, errors = _parse_inventory_csv("din,blood_type\nDIN-001,O+")
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["row"], 0)
        self.assertIn("missing required column(s)", errors[0]["reason"])
        for col in ["component", "location", "volume_ml", "collected_date", "expires_date"]:
            self.assertIn(col, errors[0]["reason"])

    def test_header_case_and_whitespace_insensitive(self):
        rows, errors = _parse_inventory_csv(
            " DIN , Blood_Type ,Component,LOCATION,Volume_ML,Collected_Date,Expires_Date\n"
            "DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01"
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)

    def test_blank_line_skipped(self):
        rows, errors = _parse_inventory_csv(
            csv_text(
                "DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01",
                ",,,,,,",
                "DIN-002,A-,Plasma,Bay 2,250,2026-08-02,2026-10-02",
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual([r["din"] for r in rows], ["DIN-001", "DIN-002"])
        # blank line consumed row 3, so DIN-002 keeps its real source line (4)
        self.assertEqual(rows[1]["row"], 4)

    def test_missing_din(self):
        rows, errors = _parse_inventory_csv(
            csv_text(",O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01")
        )
        self.assertEqual(rows, [])
        self.assertEqual(errors, [{"row": 2, "reason": "missing din"}])

    def test_invalid_blood_type(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,Z+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01")
        )
        self.assertEqual(rows, [])
        self.assertEqual(errors, [{"row": 2, "reason": "invalid blood_type 'Z+'"}])

    def test_missing_component(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,,Bay 1,280,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "missing component"}])

    def test_missing_location(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,,280,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "missing location"}])

    def test_non_numeric_volume(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,abc,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "invalid volume_ml 'abc', must be a positive whole number"}])

    def test_zero_volume_rejected(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,0,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "invalid volume_ml '0', must be a positive whole number"}])

    def test_negative_volume_rejected(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,-5,2026-08-01,2026-09-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "invalid volume_ml '-5', must be a positive whole number"}])

    def test_malformed_collected_date(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,280,08/01/2026,2026-09-01")
        )
        self.assertEqual(
            errors, [{"row": 2, "reason": "invalid collected_date '08/01/2026', expected YYYY-MM-DD"}]
        )

    def test_malformed_expires_date(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,not-a-date")
        )
        self.assertEqual(
            errors, [{"row": 2, "reason": "invalid expires_date 'not-a-date', expected YYYY-MM-DD"}]
        )

    def test_expires_before_collected_rejected(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,280,2026-08-10,2026-08-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "expires_date must be after collected_date"}])

    def test_expires_equal_to_collected_rejected(self):
        rows, errors = _parse_inventory_csv(
            csv_text("DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-08-01")
        )
        self.assertEqual(errors, [{"row": 2, "reason": "expires_date must be after collected_date"}])

    def test_partial_success_mixed_valid_and_invalid_rows(self):
        rows, errors = _parse_inventory_csv(
            csv_text(
                "DIN-001,O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01",
                "DIN-002,Z+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01",
                "DIN-003,A-,Plasma,Bay 2,250,2026-08-02,2026-10-02",
                ",O+,Packed RBC,Bay 1,280,2026-08-01,2026-09-01",
            )
        )
        self.assertEqual([r["din"] for r in rows], ["DIN-001", "DIN-003"])
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0]["row"], 3)
        self.assertEqual(errors[1]["row"], 5)


if __name__ == "__main__":
    unittest.main()
