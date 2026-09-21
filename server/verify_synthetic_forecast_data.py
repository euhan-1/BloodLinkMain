"""
SYNTHETIC DATA VERIFICATION — Stage 1 check, per explicit instruction:
"build a quick way to verify it looks realistic ... before we build anything
on top of it."

Two outputs:
1. A printed statistical summary: mean units during dengue-season months
   (Jun-Oct) vs. non-dengue months, per blood type, over the full synthetic
   series in `synthetic_inventory_snapshots`.
2. A JSON dump of the full daily series (all 8 blood types) written to the
   scratchpad, consumed by a standalone HTML chart for visual inspection.

Does not touch inventory_snapshots or any real data. Read-only against
synthetic_inventory_snapshots.
"""

import json
import statistics
from pathlib import Path

from sqlalchemy import text

from database import engine

DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}
OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\synthetic_series.json"
)


def main():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT snapshot_date, blood_type, units "
                "FROM synthetic_inventory_snapshots ORDER BY blood_type, snapshot_date"
            )
        ).mappings().all()

    if not rows:
        raise RuntimeError("synthetic_inventory_snapshots is empty — run generate_synthetic_forecast_data.py first")

    by_type: dict[str, list[dict]] = {}
    for row in rows:
        by_type.setdefault(row["blood_type"], []).append(
            {"date": row["snapshot_date"].isoformat(), "units": row["units"]}
        )

    print(f"{'blood_type':<10} {'dengue_mean':>12} {'non_dengue_mean':>16} {'diff_%':>8} {'min':>6} {'max':>6}")
    print("-" * 62)
    for blood_type, series in by_type.items():
        dengue_vals = [
            r["units"] for r in series if int(r["date"][5:7]) in DENGUE_SEASON_MONTHS
        ]
        non_dengue_vals = [
            r["units"] for r in series if int(r["date"][5:7]) not in DENGUE_SEASON_MONTHS
        ]
        dengue_mean = statistics.mean(dengue_vals)
        non_dengue_mean = statistics.mean(non_dengue_vals)
        diff_pct = (dengue_mean - non_dengue_mean) / non_dengue_mean * 100
        all_vals = [r["units"] for r in series]
        print(
            f"{blood_type:<10} {dengue_mean:>12.1f} {non_dengue_mean:>16.1f} "
            f"{diff_pct:>7.1f}% {min(all_vals):>6} {max(all_vals):>6}"
        )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(by_type))
    print(f"\nwrote full series to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
