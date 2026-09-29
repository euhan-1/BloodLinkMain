"""Undo of an inventory upload after some of its units were transferred.

Regression: confirm_receipt moves a unit to the recipient and clears its
reservation but keeps upload_history_id, and the undo selected by that tag
alone, so undoing the supplier's upload deleted units the recipient now held.
Also covers the second rule: a unit that existed before the upload is never
deleted by its undo (its prior values aren't recorded, so it is left as is).

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import io
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import text
from starlette.datastructures import UploadFile

import main as app
from database import engine

TODAY = app.business_today()


def q(sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


class UndoAfterTransferTests(unittest.TestCase):
    def test_undo_leaves_transferred_and_preexisting_units(self):
        tag = uuid.uuid4().hex[:6].upper()
        with engine.begin() as c:
            bank, hospital = [
                c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                               "VALUES (:n, :t, true, true) RETURNING id"), {"n": f"UndoXfer {tag} {t}", "t": t}).scalar()
                for t in ("bloodbank", "hospital")
            ]
            # Stock the bank already held before the upload.
            c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, "
                           "expires_date, facility_id, created_at) VALUES (:d, 'O+', 'Whole Blood', 'Fridge A', 450, "
                           ":col, :exp, :f, now() - interval '1 day')"),
                      {"d": f"{tag}-OLD", "col": TODAY - timedelta(days=2), "exp": TODAY + timedelta(days=60), "f": bank})

        col = TODAY - timedelta(days=2)
        csv_text = "din,blood_type,component,location,volume_ml,collected_date,expires_date\n" + "\n".join(
            f"{din},O+,Whole Blood,{loc},450,{col},{TODAY + timedelta(days=days)}"
            for din, loc, days in [(f"{tag}-1", "Fridge A", 10), (f"{tag}-2", "Fridge A", 11),
                                   (f"{tag}-3", "Fridge A", 30), (f"{tag}-OLD", "Fridge B", 60)]
        )
        body = asyncio.run(app.upload_inventory(
            file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="u.csv"), facility_id=bank, uploaded_by=None))
        self.assertEqual(body["rows_processed"], 4)
        upload_id = q("SELECT id FROM upload_history WHERE facility_id = :f", f=bank)[0][0]

        req = app.create_request(app.CreateRequestBody(supplying_facility_id=bank, blood_type="O+", quantity=2,
                                                       emergency_type="trauma"), requesting_facility_id=hospital)
        app.accept_request(req["id"], facility_id=bank)
        app.confirm_release(req["id"], facility_id=bank)  # FEFO: -1 and -2 expire first
        app.confirm_receipt(req["id"], facility_id=hospital)

        result = app.undo_upload(upload_id, facility_id=bank)

        self.assertEqual({r[0] for r in q("SELECT din FROM blood_units WHERE facility_id = :f", f=hospital)},
                         {f"{tag}-1", f"{tag}-2"})
        self.assertEqual(q("SELECT location FROM blood_units WHERE din = :d AND facility_id = :f", d=f"{tag}-OLD", f=bank),
                         [("Fridge B",)])
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE din = :d", d=f"{tag}-3")[0][0], 0)
        self.assertEqual(result["counts"], {"created_removed": 1, "modified_left_unchanged": 1, "skipped_transferred": 2})


if __name__ == "__main__":
    unittest.main()
