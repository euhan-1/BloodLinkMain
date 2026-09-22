from pathlib import Path

from sqlalchemy import text

from database import engine

# Minimum/maximum safe-stock levels per blood type. These are a policy/config
# decision a facility would set, not something derived from inventory data —
# starting from the values used in the original design mockup as placeholder
# defaults (maximum is a placeholder 2x the minimum, not a real capacity figure).
# Must match DEFAULT_THRESHOLDS in main.py (used to seed a newly-created
# facility via POST /admin/facilities) so a fresh-install facility and one
# created later through the app start out identically.
THRESHOLDS = [
    ("A+", 80, 160),
    ("A-", 50, 100),
    ("B+", 60, 120),
    ("B-", 40, 80),
    ("O+", 100, 200),
    ("O-", 80, 160),
    ("AB+", 30, 60),
    ("AB-", 25, 50),
]


def main():
    """Seeds default thresholds for every facility that doesn't already have
    any — per-facility since migrate_scope_thresholds_to_facility.py (was a
    single global row set before that). Safe to re-run: only fills in
    facilities missing rows, never overwrites a facility's own edited values."""
    schema_sql = Path(__file__).parent.joinpath("schema_dashboard.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

        facility_ids = [row[0] for row in conn.execute(text("SELECT id FROM facilities")).all()]
        already_seeded = {
            row[0] for row in conn.execute(text("SELECT DISTINCT facility_id FROM blood_type_thresholds")).all()
        }
        to_seed = [fid for fid in facility_ids if fid not in already_seeded]

        if not to_seed:
            print(f"all {len(facility_ids)} facilities already have thresholds, skipping seed")
            return

        rows = [
            {"facility_id": facility_id, "blood_type": blood_type, "minimum_units": minimum_units, "maximum_units": maximum_units}
            for facility_id in to_seed
            for blood_type, minimum_units, maximum_units in THRESHOLDS
        ]
        conn.execute(
            text(
                "INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) "
                "VALUES (:facility_id, :blood_type, :minimum_units, :maximum_units)"
            ),
            rows,
        )
        print(f"seeded {len(THRESHOLDS)} thresholds each for {len(to_seed)} facilities ({len(rows)} rows)")


if __name__ == "__main__":
    main()
