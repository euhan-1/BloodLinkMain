"""End-to-end walk of the forecast feature on a DISPOSABLE facility (Northside
itself is not touched): create facility + Northside's thresholds + a copy of its
current units, upload the 180-day history through the real HTTP endpoint, GET
/forecast twice, inspect the cache, then delete everything.
Run from repo root: server\\.venv\\Scripts\\python.exe docs\\methodology\\e2e_forecast_walk.py
Calls the route handler functions directly (same code the HTTP routes run;
httpx/TestClient is not installed) with facility_id passed as the JWT would."""
import json, sys, time, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "server"))
import asyncio, io
from starlette.datastructures import UploadFile
from sqlalchemy import text

import main as app
from database import engine

tag = uuid.uuid4().hex[:6]
with engine.begin() as c:
    fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed, latitude, longitude) "
                         "VALUES (:n,'bloodbank',true,true,14.6499,120.9822) RETURNING id"), {"n": f"E2E Walk {tag}"}).scalar()
    c.execute(text("INSERT INTO blood_type_thresholds (facility_id,blood_type,minimum_units,maximum_units) "
                   "SELECT :f, blood_type, minimum_units, maximum_units FROM blood_type_thresholds WHERE facility_id=5"), {"f": fid})
    c.execute(text("INSERT INTO blood_units (din,blood_type,component,location,volume_ml,collected_date,expires_date,facility_id) "
                   "SELECT 'E2E'||:t||'-'||id, blood_type,component,location,volume_ml,collected_date,expires_date,:f "
                   "FROM blood_units WHERE facility_id=5"), {"t": tag, "f": fid})

report = {"facility_id": fid}
try:
    def snap():
        with engine.connect() as c:
            return dict(c.execute(text("SELECT count(DISTINCT snapshot_date) d, min(snapshot_date) lo, max(snapshot_date) hi FROM inventory_snapshots WHERE facility_id=:f"), {"f": fid}).mappings().first())
    report["before"] = {"forecast_source": app.get_forecast(facility_id=fid)["forecast_source"], **snap()}
    with engine.begin() as c:  # undo that first call's today-snapshot so the upload starts from "no history"
        c.execute(text("DELETE FROM inventory_snapshots WHERE facility_id=:f"), {"f": fid})
        c.execute(text("DELETE FROM facility_forecast_cache WHERE facility_id=:f"), {"f": fid})
    t0 = time.time()
    up = asyncio.run(app.upload_historical_inventory_snapshots(
        file=UploadFile(file=io.BytesIO(open(HERE / "northside_180d_history.csv", "rb").read()), filename="bloodbank_history.csv"),
        facility_id=fid, uploaded_by=None))
    report["upload"] = {"body": up, "secs": round(time.time() - t0, 2)}
    report["after_upload"] = snap()
    t0 = time.time(); j = app.get_forecast(facility_id=fid); s1 = time.time() - t0
    t0 = time.time(); j2 = app.get_forecast(facility_id=fid); s2 = time.time() - t0
    report["first_get"] = {"secs": round(s1, 2)}
    report["second_get"] = {"secs": round(s2, 2), "identical_body": j == j2}
    report["response"] = {k: j.get(k) for k in ("forecast_source", "has_sufficient_history", "days_of_history", "sarimax_model_label",
                                                 "sarimax_fallback_types", "interval_confidence", "restock_status", "restock_recommendations")}
    report["series"] = j["series"]
    report["alerts"] = [(a["type"], a["severity"], a["days_until_threshold"]) for a in j["alerts"]]
    with engine.connect() as c:
        report["cache"] = [dict(r) for r in c.execute(text(
            "SELECT blood_type, count(*) n, min(forecast_date) lo, max(forecast_date) hi, max(trained_through_date) trained, max(model_order) model "
            "FROM facility_forecast_cache WHERE facility_id=:f GROUP BY 1 ORDER BY 1"), {"f": fid}).mappings()]
        report["snapshots_after_get"] = snap()
finally:
    with engine.begin() as c:
        for q in ("DELETE FROM notifications WHERE facility_id=:f", "DELETE FROM facility_forecast_cache WHERE facility_id=:f",
                  "DELETE FROM inventory_snapshots WHERE facility_id=:f", "DELETE FROM blood_units WHERE facility_id=:f",
                  "DELETE FROM upload_history WHERE facility_id=:f", "DELETE FROM blood_type_thresholds WHERE facility_id=:f",
                  "DELETE FROM facilities WHERE id=:f"):
            c.execute(text(q), {"f": fid})
    with engine.connect() as c:
        report["cleanup_leftover_facility_rows"] = c.execute(text("SELECT count(*) FROM facilities WHERE id=:f"), {"f": fid}).scalar()
print(json.dumps(report, indent=1, default=str))
