from sqlalchemy import text

from database import engine

ORIGIN_FACILITY_NAME = "Riverside General Hospital"


def _has_column(conn, table: str, column: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first() is not None


def main():
    with engine.begin() as conn:
        if _has_column(conn, "inventory_snapshots", "facility_id"):
            print("inventory_snapshots.facility_id already exists, skipping migration")
            return

        conn.execute(text("ALTER TABLE inventory_snapshots ADD COLUMN facility_id bigint REFERENCES facilities(id)"))

        origin = conn.execute(
            text("SELECT id FROM facilities WHERE name = :name"),
            {"name": ORIGIN_FACILITY_NAME},
        ).mappings().first()
        if origin is None:
            raise RuntimeError(f"origin facility {ORIGIN_FACILITY_NAME!r} not found")

        # Every existing row so far was recorded under the old single-tenant
        # assumption — it was always really Riverside's data, just never labeled.
        result = conn.execute(
            text("UPDATE inventory_snapshots SET facility_id = :fid WHERE facility_id IS NULL"),
            {"fid": origin["id"]},
        )
        print(f"backfilled {result.rowcount} existing snapshot rows to facility_id={origin['id']} ({ORIGIN_FACILITY_NAME})")

        conn.execute(text("ALTER TABLE inventory_snapshots ALTER COLUMN facility_id SET NOT NULL"))

        conn.execute(
            text("ALTER TABLE inventory_snapshots DROP CONSTRAINT inventory_snapshots_snapshot_date_blood_type_key")
        )
        conn.execute(
            text(
                "ALTER TABLE inventory_snapshots ADD CONSTRAINT inventory_snapshots_date_type_facility_key "
                "UNIQUE (snapshot_date, blood_type, facility_id)"
            )
        )
        print("facility_id is now NOT NULL; unique constraint now covers (snapshot_date, blood_type, facility_id)")


if __name__ == "__main__":
    main()
