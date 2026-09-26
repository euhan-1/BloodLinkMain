"""Times cold /forecast reads against a DEPLOYED backend, on a disposable facility.

Creates a throwaway blood-bank facility + user in the shared database, uploads the
180-day demo history through the API, then GETs /forecast repeatedly until no blood
type is pending, printing each request's status and duration. Deletes everything it
created. Use it before/after a deploy to compare cold-fit cost (e.g. the
single-threaded-BLAS change), or to see how long a fully populated panel takes.

Run from server/:
    .venv\\Scripts\\python.exe measure_deployed_forecast.py [--base https://...onrender.com] [--types O+]
"""
import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import text

import auth
from database import engine

CSV = Path(__file__).resolve().parent.parent / "docs" / "methodology" / "northside_180d_history.csv"


def call(method, url, token=None, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers={**(headers or {}), **({"Authorization": f"Bearer {token}"} if token else {})})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read() or b"null"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://bloodlink-backend-t688.onrender.com")
    ap.add_argument("--types", nargs="*", help="limit the upload to these blood types (default: all 8)")
    args = ap.parse_args()

    lines = CSV.read_text().splitlines()
    if args.types:
        lines = lines[:1] + [l for l in lines[1:] if l.split(",")[1] in args.types]
    body = "\n".join(lines).encode()

    tag = uuid.uuid4().hex[:6]
    email, password = f"zz.measure.{tag}@example.com", "MeasureTmp1!"
    with engine.begin() as c:
        fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed, address, department, doh_license_number) "
                             "VALUES (:n, 'bloodbank', true, true, 'x', 'x', 'x') RETURNING id"), {"n": f"ZZ-MEASURE-{tag}"}).scalar()
        c.execute(text("INSERT INTO users (email, password_hash, facility_id, role, must_change_password) VALUES (:e, :h, :f, 'staff', false)"),
                  {"e": email, "h": auth.hash_password(password), "f": fid})
    try:
        _, login, _ = call("POST", f"{args.base}/auth/login", data=json.dumps({"email": email, "password": password}).encode(),
                           headers={"content-type": "application/json"})
        token = login["access_token"]
        boundary = uuid.uuid4().hex
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="h.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() \
            + body + f"\r\n--{boundary}--\r\n".encode()
        status, up, secs = call("POST", f"{args.base}/forecast/historical-upload", token, multipart,
                                {"content-type": f"multipart/form-data; boundary={boundary}"})
        print(f"upload: {status} {secs:.1f}s {up and up.get('rows_processed')} rows")
        total, n = 0.0, 0
        while True:
            status, fc, secs = call("GET", f"{args.base}/forecast", token)
            n += 1
            total += secs
            pending = (fc or {}).get("pending_types")
            print(f"forecast #{n}: {status} {secs:.1f}s pending={pending}")
            if status != 200 or not pending:
                break
        print(f"fully populated after {n} request(s), {total:.1f}s of request time")
    finally:
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log",
                      "blood_units", "inventory_snapshots", "upload_history", "users"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": fid})
        print("cleaned up")


if __name__ == "__main__":
    main()
