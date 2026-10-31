"""The transfer history: GET /transfers.

Every transfer the acting facility has been part of, in both directions, as `request` + `summary` rows in the same shape
as GET /requests/{id}, with server-side filters and totals. A transfer is a request that was accepted: finished ones
(completed, failed) by default, accepted ones in progress with include_active; pending, declined and cancelled never.

What these tests prove: direction, which requests count, the received and failed counts for ledger-backed requests and for
requests that only have request_unit_failures, every filter (including the date range in Manila business dates at its
boundaries), that totals cover everything matching and not just the rows returned, that the dropdown of other facilities
is not narrowed by the filters, validation, isolation between facilities, and that a row's `request` and `summary` are the
detail read's, for both facilities.

What they do not prove: how the screen presents any of it, or anything about production.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import add, asgi_get
from tests.test_request_transitions import TransitionBase

MANILA = app.BUSINESS_TZ


def manila(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=MANILA)


class TransferBase(TransitionBase):
    def setUp(self):
        super().setUp()
        self.n = 0
        # a second hospital that also requests from self.bank, and a second bank this bank can request from
        self.bank2, self.hospital2 = make_pair(self, self.tag + "B")

    def transfer(self, supplier, requester, expiries=(10,), receive_day=1, blood_type="O+", component="Packed RBC", when=None):
        """A full release and receipt between two facilities; `when` (a datetime) then becomes the receipt time."""
        for days in expiries:
            self.n += 1
            add(supplier, f"{self.tag}-{self.n:03d}", days, blood_type=blood_type, component=component)
        req = app.create_request(app.CreateRequestBody(
            supplying_facility_id=supplier, blood_type=blood_type, component=component, quantity=len(expiries),
            emergency_type="restock"), requesting_facility_id=requester)["id"]
        app.accept_request(req, facility_id=supplier, user_id=None)
        app.confirm_release(req, facility_id=supplier, user_id=None)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=receive_day)):
            app.confirm_receipt(req, facility_id=requester, user_id=None)
        if when is not None:
            with engine.begin() as c:
                c.execute(text("UPDATE requests SET requester_confirmed_at = :w WHERE id = :r"), {"w": when, "r": req})
        return req

    def history(self, facility=None, **filters):
        return app.list_transfers(facility_id=facility or self.bank, **filters)

    def ids(self, result):
        return [t["request"]["id"] for t in result["transfers"]]


class WhichRequestsAreTransfersTests(TransferBase):
    def test_both_directions_for_a_blood_bank_and_only_requesting_for_a_hospital(self):
        supplied = self.transfer(self.bank, self.hospital)            # the bank sent this
        requested = self.transfer(self.bank2, self.bank)              # the bank received this
        mine = {t["request"]["id"]: t["direction"] for t in self.history()["transfers"]}
        self.assertEqual(mine, {supplied: "sent", requested: "received"})
        self.assertEqual({t["request"]["id"]: t["direction"] for t in self.history(self.hospital)["transfers"]}, {supplied: "received"})

    def test_the_other_facility_is_the_other_party_in_each_direction(self):
        supplied, requested = self.transfer(self.bank, self.hospital), self.transfer(self.bank2, self.bank)
        other = {t["request"]["id"]: t["other_facility_id"] for t in self.history()["transfers"]}
        self.assertEqual(other, {supplied: self.hospital, requested: self.bank2})

    def test_finished_by_default_and_accepted_ones_only_with_include_active(self):
        done = self.transfer(self.bank, self.hospital)
        add(self.bank, f"{self.tag}-ACT", 10)
        active = app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type="O+", component="Packed RBC",
                                                          quantity=1, emergency_type="restock"), requesting_facility_id=self.hospital)["id"]
        app.accept_request(active, facility_id=self.bank, user_id=None)
        self.assertEqual(self.ids(self.history()), [done])
        both = self.history(include_active=True)
        self.assertEqual(set(self.ids(both)), {done, active})
        row = next(t for t in both["transfers"] if t["request"]["id"] == active)
        self.assertEqual((row["outcome"], row["summary"]["received"], row["summary"]["failed"]), ("in_progress", None, None))

    def test_requests_that_never_became_transfers_are_never_listed(self):
        for action in ("decline", "cancel", "pending"):
            req = app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type="O+", component="Packed RBC",
                                                           quantity=1, emergency_type="restock"), requesting_facility_id=self.hospital)["id"]
            if action == "decline":
                app.decline_request(req, facility_id=self.bank, user_id=None)
            if action == "cancel":
                app.cancel_request(req, facility_id=self.hospital, user_id=None)
        self.assertEqual(self.history(include_active=True)["transfers"], [])

    def test_a_facility_sees_nothing_of_other_facilities_transfers(self):
        self.transfer(self.bank, self.hospital)
        outsider_bank, outsider_hospital = make_pair(self, self.tag + "X")
        self.assertEqual(self.history(outsider_bank)["transfers"], [])
        self.assertEqual(self.history(outsider_hospital)["transfers"], [])
        self.assertEqual(self.history(self.hospital2)["transfers"], [])


class ReceivedCountsAndOutcomesTests(TransferBase):
    def row(self, rid, facility=None):
        return next(t for t in self.history(facility)["transfers"] if t["request"]["id"] == rid)

    def test_received_in_full_partly_and_none_from_the_ledger(self):
        full = self.transfer(self.bank, self.hospital, expiries=(10, 11, 12))
        part = self.transfer(self.bank, self.hospital, expiries=(3, 6, 20), receive_day=5)   # one expires before day 5
        none = self.transfer(self.bank, self.hospital, expiries=(3, 3), receive_day=9)
        self.assertEqual((self.row(full)["outcome"], self.row(full)["summary"]), ("received_in_full", {"asked": 3, "received": 3, "failed": 0}))
        self.assertEqual((self.row(part)["outcome"], self.row(part)["summary"]), ("partly_received", {"asked": 3, "received": 2, "failed": 1}))
        self.assertEqual((self.row(none)["outcome"], self.row(none)["summary"]), ("none_received", {"asked": 2, "received": 0, "failed": 2}))

    def test_a_request_released_before_the_ledger_uses_request_unit_failures(self):
        part = self.transfer(self.bank, self.hospital, expiries=(3, 6, 20), receive_day=5)
        full = self.transfer(self.bank, self.hospital, expiries=(10, 11))
        with engine.begin() as c:  # what such requests look like: no ledger rows, only request_unit_failures where units failed
            c.execute(text("DELETE FROM request_units WHERE request_id = ANY(:r)"), {"r": [part, full]})
        self.assertEqual(self.row(part)["summary"], {"asked": 3, "received": 2, "failed": 1})
        self.assertEqual(self.row(full)["summary"], {"asked": 2, "received": 2, "failed": 0})

    def test_the_row_is_the_detail_reads_request_and_summary_for_both_facilities(self):
        rid = self.transfer(self.bank, self.hospital, expiries=(3, 6, 20), receive_day=5)
        for facility in (self.bank, self.hospital):
            detail = app.get_request_detail(rid, facility_id=facility)
            row = self.row(rid, facility)
            self.assertEqual(json.dumps(row["request"], default=str, sort_keys=True), json.dumps(detail["request"], default=str, sort_keys=True))
            self.assertEqual(row["summary"], detail["summary"])


class FilterTests(TransferBase):
    def test_other_facility(self):
        to_h1, to_h2 = self.transfer(self.bank, self.hospital), self.transfer(self.bank, self.hospital2)
        self.assertEqual(self.ids(self.history(other_facility_id=self.hospital)), [to_h1])
        self.assertEqual(self.ids(self.history(other_facility_id=self.hospital2)), [to_h2])

    def test_blood_type_and_component(self):
        a = self.transfer(self.bank, self.hospital, blood_type="A+")
        o = self.transfer(self.bank, self.hospital, blood_type="O+")
        p = self.transfer(self.bank, self.hospital, blood_type="O+", component="Platelets", expiries=(5,))
        self.assertEqual(self.ids(self.history(blood_type="A+")), [a])
        self.assertEqual(set(self.ids(self.history(blood_type="O+"))), {o, p})
        self.assertEqual(self.ids(self.history(component="Platelets")), [p])
        self.assertEqual(self.ids(self.history(blood_type="O+", component="Packed RBC")), [o])

    def test_outcome_including_expired_in_transit(self):
        full = self.transfer(self.bank, self.hospital, expiries=(10, 11))
        part = self.transfer(self.bank, self.hospital, expiries=(3, 20), receive_day=5)
        none = self.transfer(self.bank, self.hospital, expiries=(3,), receive_day=9)
        self.assertEqual(self.ids(self.history(outcome="received_in_full")), [full])
        self.assertEqual(self.ids(self.history(outcome="partly_received")), [part])
        self.assertEqual(self.ids(self.history(outcome="none_received")), [none])
        self.assertEqual(set(self.ids(self.history(outcome="expired_in_transit"))), {part, none})

    def test_date_range_is_in_manila_business_dates_and_inclusive_at_both_ends(self):
        late_oct = self.transfer(self.bank, self.hospital, when=manila(2026, 10, 31, 23, 30))   # 15:30 UTC on 31 Oct
        early_nov = self.transfer(self.bank, self.hospital, when=manila(2026, 11, 1, 0, 30))    # 16:30 UTC on 31 Oct: still the 31st in UTC
        mid_oct = self.transfer(self.bank, self.hospital, when=manila(2026, 10, 15))
        oct_only = self.history(date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 31).date())
        self.assertEqual(set(self.ids(oct_only)), {late_oct, mid_oct})   # the one 30 minutes into 1 Nov Manila is November
        nov_only = self.history(date_from=datetime(2026, 11, 1).date())
        self.assertEqual(self.ids(nov_only), [early_nov])
        self.assertEqual(self.ids(self.history(date_from=datetime(2026, 10, 31).date(), date_to=datetime(2026, 10, 31).date())), [late_oct])
        self.assertEqual(self.ids(self.history(date_to=datetime(2026, 10, 14).date())), [])

    def test_an_in_progress_transfer_is_dated_by_its_release(self):
        add(self.bank, f"{self.tag}-REL", 10)
        rid = app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type="O+", component="Packed RBC",
                                                       quantity=1, emergency_type="restock"), requesting_facility_id=self.hospital)["id"]
        app.accept_request(rid, facility_id=self.bank, user_id=None)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET supplier_confirmed_at = :w WHERE id = :r"), {"w": manila(2026, 3, 3), "r": rid})
        self.assertEqual(self.ids(self.history(include_active=True, date_from=datetime(2026, 3, 1).date(), date_to=datetime(2026, 3, 31).date())), [rid])

    def test_filters_combine(self):
        want = self.transfer(self.bank, self.hospital, blood_type="A+", when=manila(2026, 10, 10))
        self.transfer(self.bank, self.hospital2, blood_type="A+", when=manila(2026, 10, 10))
        self.transfer(self.bank, self.hospital, blood_type="B+", when=manila(2026, 10, 10))
        self.transfer(self.bank, self.hospital, blood_type="A+", when=manila(2026, 9, 10))
        got = self.history(other_facility_id=self.hospital, blood_type="A+", date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 31).date())
        self.assertEqual(self.ids(got), [want])

    def test_newest_first(self):
        old = self.transfer(self.bank, self.hospital, when=manila(2026, 1, 5))
        new = self.transfer(self.bank, self.hospital, when=manila(2026, 8, 5))
        mid = self.transfer(self.bank, self.hospital, when=manila(2026, 4, 5))
        self.assertEqual(self.ids(self.history()), [new, mid, old])


class TotalsAndFacilityChoicesTests(TransferBase):
    def test_totals_count_units_by_direction_and_expired_in_transit(self):
        self.transfer(self.bank, self.hospital, expiries=(10, 11, 12))                     # sent 3
        self.transfer(self.bank, self.hospital2, expiries=(3, 6, 20), receive_day=5)       # sent 2, 1 expired
        self.transfer(self.bank2, self.bank, expiries=(10, 11))                            # received 2
        totals = self.history()["totals"]
        self.assertEqual(totals, {"transfers": 3, "units_sent": 5, "units_received": 2, "units_failed": 1, "transfers_with_expired_units": 1})
        self.assertEqual(self.history(self.hospital)["totals"],
                         {"transfers": 1, "units_sent": 0, "units_received": 3, "units_failed": 0, "transfers_with_expired_units": 0})

    def test_totals_cover_everything_matching_not_just_the_rows_returned(self):
        for _ in range(3):
            self.transfer(self.bank, self.hospital, expiries=(10, 11))
        limited = self.history(limit=1)
        self.assertEqual(len(limited["transfers"]), 1)
        self.assertEqual((limited["total"], limited["truncated"], limited["totals"]["units_sent"]), (3, True, 6))
        self.assertFalse(self.history()["truncated"])

    def test_totals_follow_the_filters(self):
        self.transfer(self.bank, self.hospital, blood_type="A+", expiries=(10, 11))
        self.transfer(self.bank, self.hospital, blood_type="O+", expiries=(10,))
        self.assertEqual(self.history(blood_type="A+")["totals"]["units_sent"], 2)

    def test_in_progress_transfers_add_to_the_count_but_not_to_any_unit_total(self):
        add(self.bank, f"{self.tag}-IP", 10)
        rid = app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type="O+", component="Packed RBC",
                                                       quantity=1, emergency_type="restock"), requesting_facility_id=self.hospital)["id"]
        app.accept_request(rid, facility_id=self.bank, user_id=None)
        totals = self.history(include_active=True)["totals"]
        self.assertEqual((totals["transfers"], totals["units_sent"], totals["units_received"], totals["units_failed"]), (1, 0, 0, 0))

    def test_the_facility_choices_are_not_narrowed_by_the_filters(self):
        self.transfer(self.bank, self.hospital, blood_type="A+")
        self.transfer(self.bank, self.hospital2, blood_type="O+")
        filtered = self.history(blood_type="A+", other_facility_id=self.hospital)
        self.assertEqual({f["id"] for f in filtered["other_facilities"]}, {self.hospital, self.hospital2})
        names = [f["name"].lower() for f in filtered["other_facilities"]]
        self.assertEqual(names, sorted(names))

    def test_each_facility_is_listed_once(self):
        self.transfer(self.bank, self.hospital)
        self.transfer(self.bank, self.hospital)
        self.assertEqual([f["id"] for f in self.history()["other_facilities"]], [self.hospital])

    def test_nothing_at_all_is_an_empty_answer_not_an_error(self):
        empty = self.history(self.hospital2)
        self.assertEqual((empty["transfers"], empty["total"], empty["truncated"], empty["other_facilities"]), ([], 0, False, []))
        self.assertEqual(empty["totals"], {"transfers": 0, "units_sent": 0, "units_received": 0, "units_failed": 0, "transfers_with_expired_units": 0})


class RefusedRequestsTests(TransferBase):
    """Declined and cancelled requests never became transfers: hidden by default, listed only when chosen as the outcome."""

    def request(self, supplier=None, requester=None, blood_type="O+", component="Packed RBC", quantity=2):
        return app.create_request(app.CreateRequestBody(
            supplying_facility_id=supplier or self.bank, blood_type=blood_type, component=component, quantity=quantity,
            emergency_type="trauma"), requesting_facility_id=requester or self.hospital)["id"]

    def declined(self, **kw):
        rid = self.request(**kw)
        app.decline_request(rid, facility_id=kw.get("supplier") or self.bank, user_id=None)
        return rid

    def cancelled(self, **kw):
        rid = self.request(**kw)
        app.cancel_request(rid, facility_id=kw.get("requester") or self.hospital, user_id=None)
        return rid

    def test_they_are_not_listed_by_default_and_choosing_one_lists_only_that_kind(self):
        done = self.transfer(self.bank, self.hospital)
        refused, withdrawn = self.declined(), self.cancelled()
        self.assertEqual(self.ids(self.history()), [done])
        self.assertEqual(self.ids(self.history(include_active=True)), [done])
        self.assertEqual(self.ids(self.history(outcome="declined")), [refused])
        self.assertEqual(self.ids(self.history(outcome="cancelled")), [withdrawn])

    def test_a_refused_row_has_no_units_not_zero_and_is_labelled_by_what_happened_to_it(self):
        rid = self.declined(quantity=3)
        row = self.history(outcome="declined")["transfers"][0]
        self.assertEqual(row["summary"], {"asked": 3, "received": None, "failed": None})
        self.assertEqual((row["outcome"], row["request"]["id"], row["request"]["status"]), ("declined", rid, "declined"))
        withdrawn = self.cancelled(quantity=4)
        row = self.history(outcome="cancelled")["transfers"][0]
        self.assertEqual((row["outcome"], row["summary"]), ("cancelled", {"asked": 4, "received": None, "failed": None}))

    def test_both_facilities_see_the_same_refused_request_in_their_own_direction(self):
        rid = self.declined()
        self.assertEqual([(t["request"]["id"], t["direction"]) for t in self.history(self.bank, outcome="declined")["transfers"]], [(rid, "sent")])
        self.assertEqual([(t["request"]["id"], t["direction"]) for t in self.history(self.hospital, outcome="declined")["transfers"]], [(rid, "received")])

    def test_a_refused_request_is_dated_by_when_it_was_requested(self):
        rid = self.declined()
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET created_at = :w, declined_at = :d WHERE id = :r"),
                      {"w": manila(2026, 10, 5), "d": manila(2026, 11, 20), "r": rid})
        october = self.history(outcome="declined", date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 31).date())
        self.assertEqual(self.ids(october), [rid])
        self.assertEqual(self.history(outcome="declined", date_from=datetime(2026, 11, 1).date())["transfers"], [])

    def test_the_other_filters_still_apply(self):
        want = self.declined(blood_type="A+")
        self.declined(blood_type="B+")
        self.declined(supplier=self.bank2, requester=self.hospital, blood_type="A+")
        self.assertEqual(self.ids(self.history(outcome="declined", blood_type="A+")), [want])
        self.assertEqual(self.ids(self.history(outcome="declined", blood_type="A+", other_facility_id=self.hospital)), [want])

    def test_totals_count_the_requests_and_no_units(self):
        self.declined(quantity=5)
        self.declined(quantity=2)
        self.assertEqual(self.history(outcome="declined")["totals"],
                         {"transfers": 2, "units_sent": 0, "units_received": 0, "units_failed": 0, "transfers_with_expired_units": 0})

    def test_the_facility_choices_include_a_facility_that_only_ever_had_a_refused_request(self):
        self.transfer(self.bank, self.hospital)
        self.declined(requester=self.hospital2)  # hospital2's only request to this bank was refused
        everyone = {self.hospital, self.hospital2}
        self.assertEqual({f["id"] for f in self.history()["other_facilities"]}, everyone)
        self.assertEqual({f["id"] for f in self.history(outcome="declined")["other_facilities"]}, everyone)
        self.assertEqual({f["id"] for f in self.history(outcome="declined", other_facility_id=self.hospital2)["other_facilities"]}, everyone)

    def test_a_facility_sees_only_its_own_refused_requests(self):
        self.declined()
        outsider_bank, _ = make_pair(self, self.tag + "Y")
        self.assertEqual(self.history(outsider_bank, outcome="declined")["transfers"], [])
        self.assertEqual(self.history(self.hospital2, outcome="declined")["transfers"], [])


class ValidationAndRouteTests(TransferBase):
    def refused(self, **filters):
        with self.assertRaises(app.HTTPException) as ctx:
            self.history(**filters)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_unknown_values_are_refused(self):
        self.refused(blood_type="C+")
        self.refused(component="Whole Blood")
        self.refused(outcome="lost")
        self.refused(date_from=datetime(2026, 11, 2).date(), date_to=datetime(2026, 11, 1).date())

    def test_the_route_answers_through_the_real_app_and_rejects_a_bad_date(self):
        app.app.dependency_overrides[app.get_acting_facility_id] = lambda: self.bank
        self.addCleanup(app.app.dependency_overrides.pop, app.get_acting_facility_id, None)
        self.transfer(self.bank, self.hospital)
        status, body = asgi_get("/transfers", {"date_from": "2026-01-01", "include_active": "true"})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["transfers"]), 1)
        status, _ = asgi_get("/transfers", {"date_from": "not-a-date"})
        self.assertEqual(status, 422)

    def test_the_route_does_not_collide_with_the_request_routes(self):
        order = [r.path for r in app.app.routes if hasattr(r, "path")]
        self.assertIn("/transfers", order)
        self.assertLess(order.index("/requests/incoming"), order.index("/requests/{request_id}"))
