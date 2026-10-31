"""The component a request asks for, and the release rule built on it.

Before: confirm_release and the nearby search matched on blood type alone, so a request for packed red cells could be
filled with platelets, and a bank holding only platelets was reported "Available". Now a request names its component
(Packed RBC or Platelets, required, no default); release reserves only units of exactly that component, and the search
counts only that component. A unit expiring on the release day is never released (the hard floor), and each component has
a named margin above it (main.MIN_DAYS_TO_EXPIRY_AT_RELEASE), applied before FEFO picks.

What these tests prove: the behaviour above, on real Postgres through the same functions the routes call, the 422s through
the real ASGI app, and that the frontend's component list is the server's.

What they do not prove: that the margins are clinically right (they are configuration to be confirmed, and these tests
only pin how the setting is applied), anything about production data, or what the screen shows.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import json
import pathlib
import re
import unittest
import uuid
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

from pydantic import ValidationError
from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_priority import asgi_post


def add(facility_id, din, expires_in_days, component="Packed RBC", blood_type="O+"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, "
                       "expires_date, facility_id) VALUES (:d, :bt, :c, 'Fridge A', 280, :col, :exp, :f)"),
                  {"d": din, "bt": blood_type, "c": component, "col": TODAY - timedelta(days=3),
                   "exp": TODAY + timedelta(days=expires_in_days), "f": facility_id})


def accepted_request(bank, hospital, quantity=1, component="Packed RBC", blood_type="O+"):
    req = app.create_request(app.CreateRequestBody(supplying_facility_id=bank, blood_type=blood_type, component=component,
                                                   quantity=quantity, emergency_type="trauma"), requesting_facility_id=hospital)
    app.accept_request(req["id"], facility_id=bank, user_id=None)
    return req["id"]


def reserved_dins(request_id):
    return sorted(r[0] for r in q("SELECT din FROM blood_units WHERE reserved_for_request_id = :r", r=request_id))


class ComponentValidationTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        app.app.dependency_overrides[app.get_acting_facility_id] = lambda: self.hospital  # skip login; the route is the real one
        self.addCleanup(app.app.dependency_overrides.pop, app.get_acting_facility_id, None)

    def body(self, **over):
        return {"supplying_facility_id": self.bank, "blood_type": "A+", "component": "Packed RBC", "quantity": 1,
                "emergency_type": "trauma", **over}

    def test_the_two_production_components_are_the_whole_list(self):
        self.assertEqual(app.COMPONENTS, ("Packed RBC", "Platelets"))
        self.assertEqual(app.RED_CELL_COMPONENTS, {"Packed RBC"})

    def test_every_component_with_a_margin_is_a_known_component(self):
        self.assertTrue(set(app.MIN_DAYS_TO_EXPIRY_AT_RELEASE) <= set(app.COMPONENTS))

    def test_the_model_accepts_the_listed_components_and_nothing_else(self):
        for good in app.COMPONENTS:
            with self.subTest(good=good):
                self.assertEqual(app.CreateRequestBody(**self.body(component=good)).component, good)
        for bad in ["packed rbc", "Packed RBC ", "Whole Blood", "Plasma", "", "null"]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                app.CreateRequestBody(**self.body(component=bad))

    def test_there_is_no_default_component(self):
        payload = self.body()
        del payload["component"]
        with self.assertRaises(ValidationError):
            app.CreateRequestBody(**payload)
        status, response = asgi_post("/requests", payload)
        self.assertEqual(status, 422, response)
        self.assertIn("component", json.dumps(response))
        self.assertEqual(q("SELECT count(*) FROM requests WHERE requesting_facility_id = :h", h=self.hospital)[0][0], 0)

    def test_the_api_answers_422_and_stores_nothing_for_an_unknown_component(self):
        status, response = asgi_post("/requests", self.body(component="Whole Blood"))
        self.assertEqual(status, 422, response)
        self.assertIn("component must be one of", json.dumps(response))
        self.assertEqual(q("SELECT count(*) FROM requests WHERE requesting_facility_id = :h", h=self.hospital)[0][0], 0)

    def test_the_component_is_stored_and_listed_on_both_sides(self):
        for comp in app.COMPONENTS:
            created = app.create_request(app.CreateRequestBody(**self.body(component=comp)), requesting_facility_id=self.hospital)
            self.assertEqual(created["component"], comp)
            self.assertEqual(q("SELECT component FROM requests WHERE id = :r", r=created["id"])[0][0], comp)
            mine = next(r for r in app.list_requests(facility_id=self.hospital) if r["id"] == created["id"])
            theirs = next(r for r in app.list_incoming_requests(facility_id=self.bank) if r["id"] == created["id"])
            self.assertEqual((mine["component"], theirs["component"]), (comp, comp))


class ReleaseMatchesComponentTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def test_platelets_never_fill_a_packed_rbc_request_even_when_they_expire_first(self):
        # FEFO alone would take the platelet unit (it expires sooner). The component filter has to come first.
        add(self.bank, self.din("PLT"), 4, component="Platelets")
        add(self.bank, self.din("RBC"), 20)
        rid = accepted_request(self.bank, self.hospital, 1, component="Packed RBC")

        app.confirm_release(rid, facility_id=self.bank, user_id=None)

        self.assertEqual(reserved_dins(rid), [self.din("RBC")])

    def test_a_platelet_request_reserves_platelets_only(self):
        add(self.bank, self.din("RBC"), 20)
        add(self.bank, self.din("PLT"), 4, component="Platelets")
        rid = accepted_request(self.bank, self.hospital, 1, component="Platelets")

        app.confirm_release(rid, facility_id=self.bank, user_id=None)

        self.assertEqual(reserved_dins(rid), [self.din("PLT")])

    def test_a_bank_with_only_the_wrong_component_cannot_release_and_reserves_nothing(self):
        add(self.bank, self.din("PLT-1"), 4, component="Platelets")
        add(self.bank, self.din("PLT-2"), 4, component="Platelets")
        rid = accepted_request(self.bank, self.hospital, 1, component="Packed RBC")

        with self.assertRaises(app.HTTPException) as ctx:
            app.confirm_release(rid, facility_id=self.bank, user_id=None)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("only 0", ctx.exception.detail)
        self.assertEqual(reserved_dins(rid), [])
        self.assertIsNone(q("SELECT supplier_confirmed_at FROM requests WHERE id = :r", r=rid)[0][0])

    def test_blood_type_is_still_matched_too(self):
        add(self.bank, self.din("A"), 20, blood_type="A+")
        rid = accepted_request(self.bank, self.hospital, 1, component="Packed RBC", blood_type="O+")
        with self.assertRaises(app.HTTPException):
            app.confirm_release(rid, facility_id=self.bank, user_id=None)

    def test_a_request_with_no_recorded_component_is_refused_with_a_clear_reason(self):
        # The 12 requests that predate the column. Inserted directly, as a migrated row would be.
        add(self.bank, self.din("RBC"), 20)
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, "
                                 "emergency_type, status) VALUES (:h, :b, 'O+', 1, 'trauma', 'accepted') RETURNING id"),
                            {"h": self.hospital, "b": self.bank}).scalar()

        with self.assertRaises(app.HTTPException) as ctx:
            app.confirm_release(rid, facility_id=self.bank, user_id=None)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("does not say which component", ctx.exception.detail)
        self.assertEqual(reserved_dins(rid), [])


class ReleaseExpiryMarginTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def test_the_cutoff_is_the_larger_of_the_floor_and_the_components_margin(self):
        day = TODAY
        self.assertEqual(app._release_expiry_cutoff("Packed RBC", day), day + timedelta(days=3))
        self.assertEqual(app._release_expiry_cutoff("Platelets", day), day + timedelta(days=1))
        with mock.patch.dict(app.MIN_DAYS_TO_EXPIRY_AT_RELEASE, {"Packed RBC": 0, "Platelets": -5}):  # below the floor
            self.assertEqual(app._release_expiry_cutoff("Packed RBC", day), day + timedelta(days=app.RELEASE_EXPIRY_FLOOR_DAYS))
            self.assertEqual(app._release_expiry_cutoff("Platelets", day), day + timedelta(days=app.RELEASE_EXPIRY_FLOOR_DAYS))
        self.assertEqual(app._release_expiry_cutoff("Something new", day), day + timedelta(days=app.RELEASE_EXPIRY_FLOOR_DAYS))
        self.assertGreaterEqual(app.RELEASE_EXPIRY_FLOOR_DAYS, 1, "the floor must exclude units expiring on the release day")

    def test_a_unit_expiring_on_the_release_day_is_never_released_whatever_the_margin(self):
        add(self.bank, self.din("TODAY"), 0)
        rid = accepted_request(self.bank, self.hospital, 1)
        with mock.patch.dict(app.MIN_DAYS_TO_EXPIRY_AT_RELEASE, clear=True):  # no margin configured at all: the floor remains
            with self.assertRaises(app.HTTPException):
                app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(rid), [])

    def test_the_floor_alone_lets_a_unit_expiring_tomorrow_through(self):
        add(self.bank, self.din("TOMORROW"), 1)
        rid = accepted_request(self.bank, self.hospital, 1)
        with mock.patch.dict(app.MIN_DAYS_TO_EXPIRY_AT_RELEASE, clear=True):
            app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(rid), [self.din("TOMORROW")])

    def test_packed_rbc_needs_its_full_margin_and_the_unit_exactly_at_the_cutoff_is_released(self):
        margin = app.MIN_DAYS_TO_EXPIRY_AT_RELEASE["Packed RBC"]
        add(self.bank, self.din("SHORT"), margin - 1)
        add(self.bank, self.din("EDGE"), margin)
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(rid), [self.din("EDGE")])

    def test_the_margin_is_applied_before_fefo_so_the_nearest_expiry_is_not_the_one_chosen(self):
        add(self.bank, self.din("TODAY"), 0)       # FEFO's first choice, and exactly what the margin must remove
        add(self.bank, self.din("LATER"), 10)
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(rid), [self.din("LATER")])

    def test_fefo_still_picks_the_earliest_of_the_units_that_qualify(self):
        for n, days in enumerate((30, 5, 12, 0)):
            add(self.bank, self.din(n), days)
        rid = accepted_request(self.bank, self.hospital, 2)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(rid), sorted([self.din(1), self.din(2)]))

    def test_each_component_has_its_own_margin(self):
        add(self.bank, self.din("PLT"), 1, component="Platelets")  # enough for platelets (margin 1)
        add(self.bank, self.din("RBC"), 1)                         # not enough for packed RBC (margin 3)
        plt = accepted_request(self.bank, self.hospital, 1, component="Platelets")
        rbc = accepted_request(self.bank, self.hospital, 1, component="Packed RBC")
        app.confirm_release(plt, facility_id=self.bank, user_id=None)
        with self.assertRaises(app.HTTPException):
            app.confirm_release(rbc, facility_id=self.bank, user_id=None)
        self.assertEqual(reserved_dins(plt), [self.din("PLT")])

    def test_a_short_release_says_how_many_units_the_margin_held_back(self):
        add(self.bank, self.din("SOON-1"), 1)
        add(self.bank, self.din("SOON-2"), 2)
        add(self.bank, self.din("OK"), 20)
        rid = accepted_request(self.bank, self.hospital, 2)
        with self.assertRaises(app.HTTPException) as ctx:
            app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertIn("only 1", ctx.exception.detail)
        self.assertIn("2 more expire within 3 days", ctx.exception.detail)
        self.assertEqual(reserved_dins(rid), [])

    def test_receipt_is_unchanged_and_still_accepts_a_unit_expiring_on_the_receipt_day(self):
        add(self.bank, self.din("EDGE"), 3)
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=3)):
            result = app.confirm_receipt(rid, facility_id=self.hospital, user_id=None)
        self.assertEqual((result["status"], result["units_transferred"]), ("completed", 1))


class NearbySearchComponentTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def search(self, blood_type="O+", component="Packed RBC", quantity=1):
        result = app.get_nearby_facilities(blood_type=blood_type, component=component, quantity=quantity, acting_facility_id=self.hospital)
        return next(r for r in result if r["id"] == self.bank)

    def test_a_bank_holding_only_platelets_is_not_available_for_packed_rbc(self):
        add(self.bank, self.din(1), 20, component="Platelets")
        rbc = self.search(component="Packed RBC")
        self.assertEqual((rbc["matching_units"], rbc["available"]), (0, False))
        plt = self.search(component="Platelets")
        self.assertEqual((plt["matching_units"], plt["available"]), (1, True))

    def test_the_search_and_the_release_agree(self):
        # The point of filtering the search: what it calls Available is what confirm_release will actually reserve.
        add(self.bank, self.din("PLT"), 20, component="Platelets")
        for comp in app.COMPONENTS:
            with self.subTest(comp=comp):
                available = self.search(component=comp)["available"]
                rid = accepted_request(self.bank, self.hospital, 1, component=comp)
                if available:
                    app.confirm_release(rid, facility_id=self.bank, user_id=None)
                    self.assertEqual(len(reserved_dins(rid)), 1)
                else:
                    with self.assertRaises(app.HTTPException):
                        app.confirm_release(rid, facility_id=self.bank, user_id=None)

    def test_units_inside_the_release_margin_are_not_counted(self):
        add(self.bank, self.din("SOON"), 2)   # releasable only with a margin of 2 or less; packed RBC needs 3
        add(self.bank, self.din("OK"), 3)
        self.assertEqual(self.search()["matching_units"], 1)
        add(self.bank, self.din("PLT"), 1, component="Platelets")  # platelets need only 1
        self.assertEqual(self.search(component="Platelets")["matching_units"], 1)

    def test_a_unit_expiring_today_is_not_counted(self):
        add(self.bank, self.din("TODAY"), 0, component="Platelets")
        self.assertEqual(self.search(component="Platelets")["matching_units"], 0)

    def test_compatible_alternatives_are_offered_for_packed_rbc_only(self):
        add(self.bank, self.din("RBC-O-"), 20, blood_type="O-")
        add(self.bank, self.din("PLT-O-"), 20, blood_type="O-", component="Platelets")
        rbc = self.search(blood_type="A+", component="Packed RBC")
        self.assertEqual([a["blood_type"] for a in rbc["compatible_alternatives"]], ["O-"])
        plt = self.search(blood_type="A+", component="Platelets")
        self.assertEqual(plt["compatible_alternatives"], [])

    def test_an_unknown_component_is_refused(self):
        with self.assertRaises(app.HTTPException) as ctx:
            app.get_nearby_facilities(blood_type="O+", component="Whole Blood", quantity=1, acting_facility_id=self.hospital)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_the_route_requires_a_component(self):
        app.app.dependency_overrides[app.get_acting_facility_id] = lambda: self.hospital
        self.addCleanup(app.app.dependency_overrides.pop, app.get_acting_facility_id, None)
        status, response = asgi_get("/facilities/nearby", {"blood_type": "O+", "quantity": 1})
        self.assertEqual(status, 422, response)
        self.assertIn("component", json.dumps(response))
        status, _ = asgi_get("/facilities/nearby", {"blood_type": "O+", "quantity": 1, "component": "Packed RBC"})
        self.assertEqual(status, 200)


def asgi_get(path, params):
    """One GET through the real ASGI app, mirroring asgi_post."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": urlencode(params).encode(), "client": ("127.0.0.1", 50000),
             "server": ("testserver", 80), "headers": [(b"host", b"testserver")]}
    asyncio.run(app.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


class FrontendContractTests(unittest.TestCase):
    def test_the_frontends_components_are_the_servers(self):
        src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "app" / "lib" / "bloodComponents.ts").read_text(encoding="utf-8")
        listed = re.search(r"export const BLOOD_COMPONENTS\s*=\s*\[([^\]]*)\]", src)
        self.assertIsNotNone(listed, "BLOOD_COMPONENTS not found in bloodComponents.ts")
        self.assertEqual(re.findall(r'"([^"]+)"', listed.group(1)), list(app.COMPONENTS))
        red = re.search(r"export const RED_CELL_COMPONENTS[^=]*=\s*\[([^\]]*)\]", src)
        self.assertEqual(set(re.findall(r'"([^"]+)"', red.group(1))), app.RED_CELL_COMPONENTS)
