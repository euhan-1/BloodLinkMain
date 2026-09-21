"""Unit tests for the historical inventory-snapshot CSV parsing logic
(Step 11A). Pure-function tests only — no DB, no HTTP, no facility_type
gating (that lives in the endpoint and is covered by the live walkthrough,
since it inherently needs a real DB row to check facility_type against).

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_historical_upload -v
"""

import datetime
import unittest

from main import _parse_historical_snapshot_csv

HEADER = "snapshot_date,blood_type,units"
TODAY = datetime.date(2026, 8, 20)


def csv_text(*data_lines: str) -> str:
    return "\n".join([HEADER, *data_lines])


class ParseHistoricalSnapshotCsvTests(unittest.TestCase):
    def test_valid_rows_parsed_with_correct_types(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-01-15,O+,120"), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["snapshot_date"], datetime.date(2026, 1, 15))
        self.assertEqual(row["blood_type"], "O+")
        self.assertEqual(row["units"], 120)
        self.assertIsInstance(row["units"], int)
        self.assertEqual(row["row"], 2)

    def test_multiple_rows_same_date_different_types(self):
        rows, errors = _parse_historical_snapshot_csv(
            csv_text("2026-01-15,O+,120", "2026-01-15,O-,45", "2026-01-16,O+,110"), TODAY
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 3)

    def test_empty_file(self):
        rows, errors = _parse_historical_snapshot_csv("", TODAY)
        self.assertEqual(rows, [])
        self.assertEqual(errors, [{"row": 0, "reason": "empty file"}])

    def test_missing_required_columns(self):
        rows, errors = _parse_historical_snapshot_csv("snapshot_date,blood_type\n2026-01-15,O+", TODAY)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["row"], 0)
        self.assertIn("missing required column(s)", errors[0]["reason"])
        self.assertIn("units", errors[0]["reason"])

    def test_header_case_and_whitespace_insensitive(self):
        rows, errors = _parse_historical_snapshot_csv(
            " Snapshot_Date , BLOOD_TYPE,Units\n2026-01-15,O+,120", TODAY
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)

    def test_blank_line_skipped(self):
        rows, errors = _parse_historical_snapshot_csv(
            csv_text("2026-01-15,O+,120", ",,", "2026-01-16,O+,110"), TODAY
        )
        self.assertEqual(errors, [])
        self.assertEqual([r["units"] for r in rows], [120, 110])
        self.assertEqual(rows[1]["row"], 4)  # blank line consumed row 3

    def test_malformed_date(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("01/15/2026,O+,120"), TODAY)
        self.assertEqual(errors, [{"row": 2, "reason": "invalid snapshot_date '01/15/2026', expected YYYY-MM-DD"}])

    def test_snapshot_date_today_rejected(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-08-20,O+,120"), TODAY)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("must be before today", errors[0]["reason"])

    def test_snapshot_date_in_future_rejected(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-09-01,O+,120"), TODAY)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("must be before today", errors[0]["reason"])

    def test_snapshot_date_yesterday_accepted(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-08-19,O+,120"), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)

    def test_invalid_blood_type(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-01-15,ZZ,120"), TODAY)
        self.assertEqual(errors, [{"row": 2, "reason": "invalid blood_type 'ZZ'"}])

    def test_non_numeric_units(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-01-15,O+,abc"), TODAY)
        self.assertEqual(errors, [{"row": 2, "reason": "invalid units 'abc', must be a whole number 0 or greater"}])

    def test_negative_units_rejected(self):
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-01-15,O+,-5"), TODAY)
        self.assertEqual(errors, [{"row": 2, "reason": "invalid units '-5', must be a whole number 0 or greater"}])

    def test_zero_units_accepted(self):
        # A real stockout day is valid history, not an error.
        rows, errors = _parse_historical_snapshot_csv(csv_text("2026-01-15,O+,0"), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["units"], 0)

    def test_partial_success_mixed_valid_and_invalid_rows(self):
        rows, errors = _parse_historical_snapshot_csv(
            csv_text(
                "2026-01-15,O+,120",
                "2026-01-16,ZZ,50",
                "2026-01-17,O-,60",
                "2026-08-20,O+,999",  # today, rejected
            ),
            TODAY,
        )
        self.assertEqual([r["snapshot_date"].isoformat() for r in rows], ["2026-01-15", "2026-01-17"])
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0]["row"], 3)
        self.assertEqual(errors[1]["row"], 5)


if __name__ == "__main__":
    unittest.main()
