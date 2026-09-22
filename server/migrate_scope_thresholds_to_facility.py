"""Makes blood_type_thresholds per-facility instead of one shared global
table — the correctness bug flagged in the deep audit: any facility editing
its own threshold was silently changing what every other facility saw too.

Idempotent (safe to re-run): does nothing if blood_type_thresholds already
has a facility_id column.

Migration, in order:
  1. Capture the current global rows (one per blood_type) in memory.
  2. Drop the existing single-column PRIMARY KEY (looked up dynamically from
     information_schema — not hardcoded, in case it isn't the default
     Postgres-assigned name).
  3. Add a nullable facility_id column.
  4. Backfill: for every existing facility, insert one row per blood_type
     copying the captured global values — this is the "nothing changes for
     anyone the day this ships" step. Values are preserved, not lost.
  5. Delete the original global rows (now superseded by the per-facility
     copies from step 4 — this is a structural schema change, not a loss of
     any business entity, so it doesn't conflict with the archive-only
     philosophy that applies to facilities/blood_units).
  6. Make facility_id NOT NULL and add the composite (facility_id,
     blood_type) primary key.

Run manually, once, against the real database (like every other
migrate_*.py script in this project):
    .venv\\Scripts\\python.exe migrate_scope_thresholds_to_facility.py
"""

from sqlalchemy import text

from database import engine


def _has_column(conn, table: str, column: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first() is not None


def _primary_key_constraint_name(conn, table: str) -> str:
    return conn.execute(
        text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            WHERE tc.table_name = :table AND tc.constraint_type = 'PRIMARY KEY'
            """
        ),
        {"table": table},
    ).scalar()


def main():
    with engine.begin() as conn:
        if _has_column(conn, "blood_type_thresholds", "facility_id"):
            print("blood_type_thresholds.facility_id already exists, skipping")
            return

        global_rows = conn.execute(
            text("SELECT blood_type, minimum_units, maximum_units FROM blood_type_thresholds")
        ).mappings().all()
        print(f"captured {len(global_rows)} existing global threshold rows")

        pk_name = _primary_key_constraint_name(conn, "blood_type_thresholds")
        if pk_name:
            conn.execute(text(f'ALTER TABLE blood_type_thresholds DROP CONSTRAINT "{pk_name}"'))
            print(f"dropped primary key constraint {pk_name!r}")

        conn.execute(text("ALTER TABLE blood_type_thresholds ADD COLUMN facility_id bigint REFERENCES facilities(id)"))

        facility_ids = [row[0] for row in conn.execute(text("SELECT id FROM facilities")).all()]
        backfill_rows = [
            {
                "facility_id": facility_id,
                "blood_type": row["blood_type"],
                "minimum_units": row["minimum_units"],
                "maximum_units": row["maximum_units"],
            }
            for facility_id in facility_ids
            for row in global_rows
        ]
        if backfill_rows:
            conn.execute(
                text(
                    "INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) "
                    "VALUES (:facility_id, :blood_type, :minimum_units, :maximum_units)"
                ),
                backfill_rows,
            )
        print(f"backfilled {len(backfill_rows)} per-facility rows ({len(facility_ids)} facilities x {len(global_rows)} blood types)")

        deleted = conn.execute(text("DELETE FROM blood_type_thresholds WHERE facility_id IS NULL")).rowcount
        print(f"removed {deleted} superseded global rows")

        conn.execute(text("ALTER TABLE blood_type_thresholds ALTER COLUMN facility_id SET NOT NULL"))
        conn.execute(text("ALTER TABLE blood_type_thresholds ADD PRIMARY KEY (facility_id, blood_type)"))
        print("blood_type_thresholds is now keyed by (facility_id, blood_type)")


if __name__ == "__main__":
    main()
