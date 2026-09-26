"""Supporting checks for the methodology docs (writes extra_checks.json):
1. weekday effect: is s=7 seasonality actually present in the demo series?
   (detrend with a centred 7-day moving average, one-way ANOVA by weekday)
2. continuity: last history day (2026-09-25) vs Northside's current usable
   stock per type in the DB (what the dashboard's 'Today' point is built from).
Run from repo root: server\\.venv\\Scripts\\python.exe docs\\methodology\\run_extra_checks.py
"""
import json, sys
from pathlib import Path

import pandas as pd
from scipy import stats
from sqlalchemy import text

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "server"))
from database import engine

df = pd.read_csv(HERE / "northside_180d_history.csv", parse_dates=["snapshot_date"])
wide = df.pivot(index="snapshot_date", columns="blood_type", values="units").sort_index()
out = {"weekday": {}, "continuity": {}}
for t in wide.columns:
    y = wide[t]
    detr = (y - y.rolling(7, center=True).mean()).dropna()
    groups = [detr[detr.index.dayofweek == d].values for d in range(7)]
    F, p = stats.f_oneway(*groups)
    means = {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]: round(float(g.mean()), 2) for d, g in enumerate(groups)}
    out["weekday"][t] = {"F": float(F), "p": float(p), "weekday_mean_dev": means,
                         "range": round(max(means.values()) - min(means.values()), 2),
                         "series_mean": round(float(y.mean()), 1)}
with engine.connect() as c:
    db = {r["blood_type"]: r["n"] for r in c.execute(text(
        "SELECT blood_type, count(*) n FROM blood_units WHERE facility_id=5 AND expires_date >= CURRENT_DATE GROUP BY 1")).mappings()}
    thr = {r["blood_type"]: r["minimum_units"] for r in c.execute(text(
        "SELECT blood_type, minimum_units FROM blood_type_thresholds WHERE facility_id=5")).mappings()}
last = wide.iloc[-1]
for t in wide.columns:
    out["continuity"][t] = {"csv_last_day": int(last[t]), "db_usable_today": int(db.get(t, 0)), "minimum_threshold": int(thr[t])}
json.dump(out, open(HERE / "extra_checks.json", "w"), indent=1)
print(json.dumps(out, indent=1))
