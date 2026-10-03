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


def main():
    with engine.begin() as conn:
        if _has_column(conn, "blood_type_thresholds", "maximum_units"):
            print("blood_type_thresholds.maximum_units already exists, skipping")
            return

        # Backfilled at 2x the existing minimum — the same placeholder ratio
        # seed_thresholds.py now uses for a fresh install — so existing rows
        # land somewhere sane until a facility sets its own value via the
        # Inventory screen's threshold editor.
        conn.execute(text("ALTER TABLE blood_type_thresholds ADD COLUMN maximum_units integer"))
        conn.execute(text("UPDATE blood_type_thresholds SET maximum_units = minimum_units * 2"))
        conn.execute(text("ALTER TABLE blood_type_thresholds ALTER COLUMN maximum_units SET NOT NULL"))
        print("added blood_type_thresholds.maximum_units (backfilled at 2x minimum_units)")


if __name__ == "__main__":
    main()
