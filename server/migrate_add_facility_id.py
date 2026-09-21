from sqlalchemy import text

from database import engine

ORIGIN_FACILITY_NAME = "Riverside General Hospital"


def main():
    with engine.begin() as conn:
        has_column = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'blood_units' AND column_name = 'facility_id'"
            )
        ).first()
        if has_column:
            print("blood_units.facility_id already exists, skipping migration")
            return

        conn.execute(text("ALTER TABLE blood_units ADD COLUMN facility_id bigint REFERENCES facilities(id)"))

        origin = conn.execute(
            text("SELECT id FROM facilities WHERE name = :name"),
            {"name": ORIGIN_FACILITY_NAME},
        ).mappings().first()
        if origin is None:
            raise RuntimeError(f"origin facility {ORIGIN_FACILITY_NAME!r} not found — run seed_facilities.py first")

        result = conn.execute(
            text("UPDATE blood_units SET facility_id = :fid WHERE facility_id IS NULL"),
            {"fid": origin["id"]},
        )
        print(f"backfilled {result.rowcount} existing rows to facility_id={origin['id']} ({ORIGIN_FACILITY_NAME})")

        conn.execute(text("ALTER TABLE blood_units ALTER COLUMN facility_id SET NOT NULL"))
        print("facility_id is now NOT NULL")


if __name__ == "__main__":
    main()
