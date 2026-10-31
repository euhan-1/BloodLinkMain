"""One definition of a releasable unit, and who is offered compatible alternatives.

RELEASABLE_UNITS_SQL (component, enough time before expiry, not reserved for another request) is what confirm_release selects
from, what the nearby search counts, and what _releasable_counts reports for anything that says how much a facility "can
give". Before, the search counted units reserved for other requests that release would then refuse, so it could promise more
than release delivers.

Compatible alternatives are red-cell compatibility and only a hospital is offered them: a blood bank is restocking, and
covering an A- restock with O- is a patient-side substitution, not a restock.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import inspect
import unittest
import uuid
from datetime import timedelta

import main as app
from database import engine
from tests.test_expired_transfer import TODAY, make_pair
from tests.test_request_component import accepted_request, add


class ReleasableCountsTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def din(self, n):
        return f"{self.tag}-{n}"

    def counts(self, component="Packed RBC", facility=None):
        with engine.connect() as c:
            return app._releasable_counts(c, component, TODAY, facility)

    def search(self, quantity=1, component="Packed RBC", blood_type="O+", as_facility=None):
        result = app.get_nearby_facilities(blood_type=blood_type, component=component, quantity=quantity,
                                           acting_facility_id=as_facility or self.hospital)
        return next(r for r in result if r["id"] == self.bank)

    def test_it_counts_what_release_would_select_and_nothing_else(self):
        add(self.bank, self.din("OK"), 20)
        add(self.bank, self.din("SOON"), 1)                          # inside the packed RBC margin
        add(self.bank, self.din("TODAY"), 0)                         # expires on the day
        add(self.bank, self.din("PLT"), 20, component="Platelets")   # another component
        add(self.bank, self.din("A"), 20, blood_type="A+")
        self.assertEqual(self.counts()[self.bank], {"O+": 1, "A+": 1})
        self.assertEqual(self.counts("Platelets")[self.bank], {"O+": 1})

    def test_a_unit_reserved_for_a_request_is_not_releasable(self):
        add(self.bank, self.din(1), 20)
        add(self.bank, self.din(2), 20)
        self.assertEqual(self.counts()[self.bank], {"O+": 2})
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        self.assertEqual(self.counts()[self.bank], {"O+": 1})

    def test_one_facility_can_be_asked_for_on_its_own(self):
        add(self.bank, self.din(1), 20)
        other_bank, _ = make_pair(self, self.tag + "B")
        add(other_bank, f"{self.tag}-OTHER", 20)
        self.assertEqual(set(self.counts()), {self.bank, other_bank})
        self.assertEqual(self.counts(facility=self.bank), {self.bank: {"O+": 1}})
        self.assertEqual(self.counts(facility=self.hospital), {})

    def test_the_search_stops_counting_units_that_release_would_refuse(self):
        add(self.bank, self.din(1), 20)
        add(self.bank, self.din(2), 20)
        first = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(first, facility_id=self.bank, user_id=None)  # one unit now reserved for another request
        found = self.search(quantity=2)
        self.assertEqual((found["matching_units"], found["available"]), (1, False))
        second = accepted_request(self.bank, self.hospital, 2)
        with self.assertRaises(app.HTTPException):
            app.confirm_release(second, facility_id=self.bank, user_id=None)  # and release agrees with the search

    def test_search_and_release_agree_across_the_margin_the_reservations_and_the_component(self):
        add(self.bank, self.din("RBC"), 20)
        add(self.bank, self.din("SOON"), 2)
        add(self.bank, self.din("PLT"), 2, component="Platelets")
        for component in app.COMPONENTS:
            with self.subTest(component):
                said = self.search(component=component)["matching_units"]
                rid = accepted_request(self.bank, self.hospital, 1, component=component)
                app.confirm_release(rid, facility_id=self.bank, user_id=None)
                from tests.test_request_component import reserved_dins
                self.assertEqual(said >= 1, len(reserved_dins(rid)) == 1)

    def test_release_and_the_helper_use_the_same_predicate_text(self):
        # A structural guard against the two drifting apart: both are built from the one constant.
        self.assertIn("RELEASABLE_UNITS_SQL", inspect.getsource(app.confirm_release))
        self.assertIn("RELEASABLE_UNITS_SQL", inspect.getsource(app._releasable_counts))
        self.assertNotIn("reserved_for_request_id", inspect.getsource(app.get_nearby_facilities))  # the search holds no copy of it


class AlternativesAreForHospitalsTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        self.other_bank, _ = make_pair(self, self.tag + "B")
        add(self.bank, f"{self.tag}-O-", 20, blood_type="O-")

    def alternatives(self, as_facility):
        found = app.get_nearby_facilities(blood_type="A+", component="Packed RBC", quantity=1, acting_facility_id=as_facility)
        return next(r for r in found if r["id"] == self.bank)["compatible_alternatives"]

    def test_a_hospital_is_offered_o_negative_for_an_a_positive_it_cannot_get(self):
        self.assertEqual([a["blood_type"] for a in self.alternatives(self.hospital)], ["O-"])

    def test_a_blood_bank_restocking_is_offered_no_alternatives(self):
        self.assertEqual(self.alternatives(self.other_bank), [])

    def test_a_bank_that_cannot_supply_the_exact_type_is_simply_unavailable_to_a_bank(self):
        found = app.get_nearby_facilities(blood_type="A+", component="Packed RBC", quantity=1, acting_facility_id=self.other_bank)
        row = next(r for r in found if r["id"] == self.bank)
        self.assertEqual((row["available"], row["compatible_alternatives"]), (False, []))
