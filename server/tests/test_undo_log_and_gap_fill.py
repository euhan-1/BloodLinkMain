"""Three fixes around historical uploads, on the scratch database (conftest.py):

1. the undo preview says how many DAYS an undo removes and how many would be left;
2. an upload saves the value it overwrites whatever wrote it (an organic row, a post-log upload, a LEGACY upload with no log), so undoing the
   newer upload restores the older value with its older tag;
3. a type missing on an upload-sourced day is carried forward in the forecast's series, not zeroed; complete data is unchanged.
"""
import itertools
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import text

import history_import as h
import main as app
from database import engine
from tests.test_bulk_upload import q, upload

TODAY = app.business_today()
HEAD = "snapshot_date,blood_type,units\n"
TYPES = sorted(app.VALID_BLOOD_TYPES)


def day(n):
    return TODAY - timedelta(days=n)


def csv(btype, pairs):
    """pairs: [(days_ago, units)]"""
    return HEAD + "\n".join(f"{day(d)},{btype},{u}" for d, u in pairs)


class Base(unittest.TestCase):
    def setUp(self):
        tag = uuid.uuid4().hex[:6]
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"UndoLog {tag}"}).scalar()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with engine.begin() as c:
            for t in ("facility_forecast_cache", "forecast_alert_state", "notifications", "inventory_snapshot_write_log", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def cleanup_rows(self):
        with engine.begin() as c:
            for t in ("inventory_snapshot_write_log", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})

    def up(self, csv_text, legacy=False):
        """An upload; legacy=True removes everything it logged, which is how an upload made before the write log existed looks."""
        before = q("SELECT coalesce(max(id), 0) FROM inventory_snapshot_write_log WHERE facility_id=:f", f=self.fid)[0][0]
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, csv_text)
        self.assertEqual(body["errors"], [])
        uid = q("SELECT max(id) FROM upload_history WHERE facility_id=:f AND upload_type='historical_stock'", f=self.fid)[0][0]
        if legacy:
            with engine.begin() as c:
                c.execute(text("DELETE FROM inventory_snapshot_write_log WHERE facility_id=:f AND id > :b"), {"f": self.fid, "b": before})
        return uid

    def undo(self, uid):
        return app.undo_upload(uid, facility_id=self.fid)

    def rows(self, btype="O+"):
        return {r[0]: (r[1], r[2]) for r in q(
            "SELECT snapshot_date, units, upload_history_id FROM inventory_snapshots WHERE facility_id=:f AND blood_type=:b", f=self.fid, b=btype)}

    def organic(self, btype, pairs):
        with engine.begin() as c:
            for d, u in pairs:
                c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) VALUES (:d, :b, :u, :f)"),
                          {"d": day(d), "b": btype, "u": u, "f": self.fid})


# ── Fix 2 ──────────────────────────────────────────────────────────────────────

class OverwrittenValueIsSavedTests(Base):
    def test_a_legacy_upload_overwritten_then_the_newer_undone_gets_its_values_and_tag_back(self):
        legacy = self.up(csv("O+", [(5, 10), (4, 10), (3, 10), (2, 10), (1, 10)]), legacy=True)
        self.assertEqual(q("SELECT count(*) FROM inventory_snapshot_write_log WHERE facility_id=:f", f=self.fid)[0][0], 0)
        newer = self.up(csv("O+", [(3, 99), (2, 99)]))
        # the overwritten values were saved under the legacy upload's tag
        self.assertEqual(sorted((r[0], r[1]) for r in q(
            "SELECT snapshot_date, units FROM inventory_snapshot_write_log WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=legacy)),
            sorted([(day(3), 10), (day(2), 10)]))
        self.undo(newer)
        rows = self.rows()
        self.assertEqual(rows[day(3)], (10, legacy))   # back, and still the legacy upload's
        self.assertEqual(rows[day(2)], (10, legacy))
        self.assertEqual(len(rows), 5)
        self.undo(legacy)                               # so undoing the first removes them all
        self.assertEqual(self.rows(), {})

    def test_the_old_behaviour_would_have_deleted_the_day(self):
        """What the fix is for: without the base entry the newer upload's undo has no winner. Shown by removing the entry."""
        legacy = self.up(csv("O+", [(3, 10), (2, 10)]), legacy=True)
        newer = self.up(csv("O+", [(3, 99)]))
        with engine.begin() as c:
            c.execute(text("DELETE FROM inventory_snapshot_write_log WHERE facility_id=:f AND upload_history_id=:u"), {"f": self.fid, "u": legacy})
        self.undo(newer)
        self.assertNotIn(day(3), self.rows())           # deleted: the day was lost
        self.assertIn(day(2), self.rows())

    def test_an_entry_is_not_duplicated_when_the_log_already_holds_that_tag(self):
        a = self.up(csv("O+", [(3, 10)]))               # post-log: its own step 2 entry exists
        self.up(csv("O+", [(3, 20)]))
        n = q("SELECT count(*) FROM inventory_snapshot_write_log WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=a)[0][0]
        self.assertEqual(n, 1)

    def test_an_organic_row_is_still_saved_and_comes_back_with_no_tag(self):
        self.organic("O+", [(3, 55)])
        a = self.up(csv("O+", [(3, 10)]))
        b = self.up(csv("O+", [(3, 20)]))
        self.undo(b)
        self.assertEqual(self.rows()[day(3)], (10, a))
        self.undo(a)
        self.assertEqual(self.rows()[day(3)], (55, None))

    def test_a_row_restored_by_an_undo_is_not_logged_twice_when_overwritten_again(self):
        legacy = self.up(csv("O+", [(3, 10)]), legacy=True)
        first = self.up(csv("O+", [(3, 20)]))
        self.undo(first)                                # back to legacy's 10
        second = self.up(csv("O+", [(3, 30)]))
        n = q("SELECT count(*) FROM inventory_snapshot_write_log WHERE facility_id=:f AND upload_history_id=:u", f=self.fid, u=legacy)[0][0]
        self.assertEqual(n, 1)
        self.undo(second)
        self.assertEqual(self.rows()[day(3)], (10, legacy))

    def test_the_older_upload_already_undone_before_the_newer_one(self):
        older = self.up(csv("O+", [(4, 10), (3, 10), (2, 10)]), legacy=True)
        newer = self.up(csv("O+", [(3, 99), (2, 99), (1, 99)]))
        self.undo(older)                                # the newer upload's values stay; older-only day 4 goes
        self.assertEqual(self.rows(), {day(3): (99, newer), day(2): (99, newer), day(1): (99, newer)})
        self.undo(newer)                                # nothing older to bring back: the days go, they are not resurrected
        self.assertEqual(self.rows(), {})

    def test_a_chain_of_three_undone_in_every_order(self):
        """A writes days 1-4, B days 2-5, C days 3-6. After each undo a day holds the newest surviving upload's value and tag."""
        spans = {"A": ((4, 3, 2, 1), 10), "B": ((5, 4, 3, 2), 20), "C": ((6, 5, 4, 3), 30)}
        for legacy_a in (False, True):
            for order in itertools.permutations("ABC"):
                with self.subTest(legacy_first=legacy_a, order="".join(order)):
                    self.cleanup_rows()
                    ids = {}
                    for name in "ABC":
                        days, val = spans[name]
                        ids[name] = self.up(csv("O+", [(d, val) for d in days]), legacy=(legacy_a and name == "A"))
                    alive = set("ABC")
                    for gone in order:
                        self.undo(ids[gone])
                        alive.discard(gone)
                        expected = {}
                        for name in "ABC":                       # later uploads overwrite earlier ones
                            if name in alive:
                                for d in spans[name][0]:
                                    expected[day(d)] = (spans[name][1], ids[name])
                        self.assertEqual(self.rows(), expected, f"after undoing {gone}")



# ── Fix 1 ──────────────────────────────────────────────────────────────────────

class UndoPreviewSummaryTests(Base):
    def summary(self, uid):
        return app.preview_upload_undo(uid, facility_id=self.fid)["summary"]

    def test_an_upload_that_added_every_day_says_how_many_go_and_how_many_are_left(self):
        self.organic("O+", [(60 + i, 5) for i in range(6)])
        a = self.up(csv("O+", [(d, 10) for d in range(40, 0, -1)]))
        s = self.summary(a)
        self.assertEqual((s["days_deleted"], s["days_restored"], s["days_remaining"], s["forecast_min_days"]), (40, 0, 6, 30))
        self.assertEqual((s["first_date"], s["last_date"]), (str(day(40)), str(day(1))))
        self.assertEqual((s["deleted_first_date"], s["deleted_last_date"]), (str(day(40)), str(day(1))))

    def test_days_that_stay_because_something_else_covers_them_are_not_deleted(self):
        self.organic("O+", [(60, 5)])
        a = self.up(csv("O+", [(d, 10) for d in range(40, 0, -1)]))
        self.up(csv("O+", [(3, 20), (2, 20), (1, 20)]))
        s = self.summary(a)
        self.assertEqual((s["days_deleted"], s["days_restored"], s["days_remaining"]), (37, 3, 1 + 3))
        self.assertEqual(s["deleted_last_date"], str(day(4)))

    def test_an_overwritten_day_goes_back_to_the_older_value_and_is_counted_as_restored(self):
        a = self.up(csv("O+", [(d, 10) for d in range(5, 0, -1)]), legacy=True)
        b = self.up(csv("O+", [(3, 20), (2, 20)]))
        s = self.summary(b)
        self.assertEqual((s["days_deleted"], s["days_restored"], s["days_remaining"]), (0, 2, 5))
        self.assertIsNone(s["deleted_first_date"])

    def test_a_day_is_only_deleted_when_no_type_remains_on_it(self):
        self.organic("A+", [(5, 7)])                    # another type already has day 5
        a = self.up(csv("O+", [(5, 10), (4, 10)]))
        s = self.summary(a)
        self.assertEqual((s["days_deleted"], s["days_remaining"]), (1, 1))  # day 4 goes; day 5 keeps its A+ row

    def test_the_summary_matches_what_the_undo_then_does(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.cleanup_rows()
                self.organic("O+", [(70, 5), (69, 5)])
                a = self.up(csv("O+", [(d, 10) for d in range(20, 0, -1)]), legacy=legacy)
                b = self.up(csv("O+", [(d, 20) for d in range(8, 3, -1)]))
                for uid in (b, a):
                    s = self.summary(uid)
                    self.undo(uid)
                    left = q("SELECT count(DISTINCT snapshot_date) FROM inventory_snapshots WHERE facility_id=:f", f=self.fid)[0][0]
                    self.assertEqual(left, s["days_remaining"])

    def test_the_existing_keys_are_unchanged_and_an_undone_upload_has_no_summary(self):
        a = self.up(csv("O+", [(2, 10)]))
        body = app.preview_upload_undo(a, facility_id=self.fid)
        self.assertEqual({"upload_type", "already_undone", "eligible", "blocked", "summary"}, set(body))
        self.assertEqual(body["eligible"], [{"snapshot_date": day(2), "blood_type": "O+", "units": 10}])
        self.undo(a)
        body = app.preview_upload_undo(a, facility_id=self.fid)
        self.assertTrue(body["already_undone"])
        self.assertIsNone(body["summary"])

    def test_the_inventory_preview_has_no_summary(self):
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO upload_history (facility_id, upload_type, filename, rows_processed, rows_failed, error_details) "
                                 "VALUES (:f, 'inventory', 'x.csv', 0, 0, '[]'::jsonb) RETURNING id"), {"f": self.fid}).scalar()
        self.assertNotIn("summary", app.preview_upload_undo(uid, facility_id=self.fid))


# ── Fix 3 ──────────────────────────────────────────────────────────────────────

def old_resample(type_rows, observed, end):
    """The function as it was before the change (every observed day the app's), for comparison."""
    dates = sorted(type_rows)
    if not dates:
        return []
    out, last, d = [], type_rows[dates[0]], dates[0]
    while d <= end:
        if d in type_rows:
            last = type_rows[d]
            out.append((d, last))
        elif d in observed:
            last = 0
            out.append((d, 0))
        else:
            out.append((d, last))
        d += timedelta(days=1)
    return out


def rows_for(days, skip=(), tag=1):
    """history rows (as get_forecast reads them) for eight types on `days`; skip = {(day, type)} left out."""
    return [{"snapshot_date": d, "blood_type": t, "units": 20 + (d.toordinal() + i) % 9, "upload_history_id": tag}
            for d in days for i, t in enumerate(TYPES) if (d, t) not in skip]


def series(rows, today, with_carry=True):
    observed = {r["snapshot_date"] for r in rows}
    carry = app._carry_forward_dates(rows) if with_carry else None
    by = {}
    for r in rows:
        by.setdefault(r["blood_type"], {})[r["snapshot_date"]] = r["units"]
    return {t: app._resample_daily_series(own, observed, today, carry) for t, own in by.items()}, by, observed


class GapFillTests(unittest.TestCase):
    DAYS = [date(2026, 3, 1) + timedelta(days=i) for i in range(60)]
    END = date(2026, 4, 30)

    def test_complete_data_gives_exactly_the_old_series(self):
        for tag in (1, None):            # a day from an upload, a day the app wrote
            rows = rows_for(self.DAYS, tag=tag)
            new, by, observed = series(rows, self.END)
            for t in TYPES:
                self.assertEqual(new[t], old_resample(by[t], observed, self.END), (tag, t))

    def test_complete_data_with_unrecorded_days_is_still_the_old_series(self):
        days = [d for d in self.DAYS if d.day not in (10, 11, 12)]
        rows = rows_for(days, tag=1)
        new, by, observed = series(rows, self.END)
        for t in TYPES:
            self.assertEqual(new[t], old_resample(by[t], observed, self.END))

    def test_a_skipped_upload_row_carries_the_previous_value_instead_of_a_zero(self):
        gap = date(2026, 3, 20)
        rows = rows_for(self.DAYS, skip={(gap, "O-")}, tag=1)
        new, by, observed = series(rows, self.END)
        old = old_resample(by["O-"], observed, self.END)
        before = dict(new["O-"])[gap - timedelta(days=1)]
        self.assertEqual(dict(old)[gap], 0)                  # before: a fake one-day collapse
        self.assertEqual(dict(new["O-"])[gap], before)       # now: the previous value stands
        other = {t: s for t, s in new.items() if t != "O-"}
        for t, s in other.items():
            self.assertEqual(s, old_resample(by[t], observed, self.END))

    def test_a_missing_type_on_a_day_the_app_wrote_is_still_a_real_zero(self):
        gap = date(2026, 3, 20)
        rows = rows_for(self.DAYS, skip={(gap, "O-")}, tag=None)
        new, by, observed = series(rows, self.END)
        self.assertEqual(dict(new["O-"])[gap], 0)

    def test_the_rule_is_per_day(self):
        up_gap, app_gap = date(2026, 3, 10), date(2026, 3, 20)
        rows = [r for r in rows_for(self.DAYS) if not ((r["snapshot_date"], r["blood_type"]) in ((up_gap, "O-"), (app_gap, "O-")))]
        for r in rows:
            r["upload_history_id"] = None if r["snapshot_date"] >= date(2026, 3, 15) else 1
        new, _, _ = series(rows, self.END)
        s = dict(new["O-"])
        self.assertEqual(s[up_gap], s[up_gap - timedelta(days=1)])
        self.assertEqual(s[app_gap], 0)

    def test_rows_without_the_column_are_read_the_old_way(self):
        rows = [{k: v for k, v in r.items() if k != "upload_history_id"} for r in rows_for(self.DAYS, skip={(date(2026, 3, 20), "O-")})]
        self.assertEqual(app._carry_forward_dates(rows), set())

    def test_the_chart_totals_use_the_same_fill_as_the_fit(self):
        gap = date(2026, 3, 20)
        rows = rows_for(self.DAYS, skip={(gap, "O-")}, tag=1)
        new, _, _ = series(rows, self.END)
        expect = {d: sum(dict(new[t])[d] for t in TYPES) for d in self.DAYS}
        got = {p["date"]: p["units"] for p in app._history_totals(rows, self.END, days=60)}
        self.assertEqual(got[gap.isoformat()], expect[gap])


class ForecastReadsTheTagTests(Base):
    """get_forecast itself, on the scratch database: the series handed to the fitter has no fake zero for a skipped upload row."""

    def run_forecast(self):
        seen = {}

        def fake(fid, daily_by_type, today, observed_dates=None, prefetched_cache=None):
            seen.update(daily_by_type)
            return {bt: None for bt in daily_by_type}, []

        with mock.patch.object(app, "_collect_sarimax_results", fake):
            app.get_forecast(facility_id=self.fid)
        return seen

    def test_a_skipped_row_in_an_upload_is_not_a_zero_in_the_forecast_input(self):
        lines = [HEAD.strip()]
        for d in range(45, 0, -1):
            for t in TYPES:
                if d == 20 and t == "O-":
                    lines.append(f"{day(d)},{t},-3")       # unreadable as stock: skipped by the upload
                else:
                    lines.append(f"{day(d)},{t},{30 + d % 5}")
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(lines))
        self.assertEqual([e["row"] for e in body["errors"]], [2 + (45 - 20) * 8 + TYPES.index("O-")])
        self.assertIn("we keep the previous value for that day", body["errors"][0]["reason"])
        s = dict(self.run_forecast()["O-"])
        self.assertEqual(s[day(20)], s[day(21)])
        self.assertNotEqual(s[day(20)], 0)
        self.assertEqual(len(s), 46)                          # the first day through today


class PreviewTextTests(unittest.TestCase):
    def test_a_skipped_units_row_says_the_previous_value_is_kept(self):
        p = h.parse_history_upload("csv", f"date,type,units\n{day(3)},O-,-3\n{day(2)},O-,x\n", TODAY)
        self.assertEqual(p.problems[0]["reason"], "O-: units -3 is negative; it must be 0 or more; we keep the previous value for that day.")
        self.assertTrue(p.problems[1]["reason"].startswith("O-: units 'x' is not a number"))

    def test_wide_cells_say_it_too(self):
        p = h.parse_history_upload("csv", f"Date,O+,O-\n{day(3)},5,n/a\n", TODAY)
        self.assertEqual(p.problems[0]["reason"].split(";")[-1].strip(), "we keep the previous value for that day.")
        self.assertTrue(p.problems[0]["reason"].startswith("O-:"))

    def test_problems_that_name_no_day_or_type_do_not_say_it(self):
        p = h.parse_history_upload("csv", f"date,type,units\nsoon,O-,5\n{day(3)},ZZ,5\n", TODAY)
        for e in p.problems:
            self.assertNotIn("previous value", e["reason"])
