"""Adds blood_units.archived_at — nullable, set only by
POST /inventory/archive-expired to bulk-clear the expired-units backlog.
Soft-delete only, same pattern as facilities.is_active: never a DELETE, so
historical blood_units rows are preserved. Has no bearing on any
usable/available count (those are already gated on expires_date alone,
unconditionally, everywhere stock is counted as available) — archived_at
only controls what GET /inventory shows by default.

Idempotent: safe to re-run, like the other migrate_*.py scripts in this
project.
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


def main():
    with engine.begin() as conn:
        if _has_column(conn, "blood_units", "archived_at"):
            print("blood_units.archived_at already exists, skipping")
            return
        conn.execute(text("ALTER TABLE blood_units ADD COLUMN archived_at timestamptz"))
        print("added blood_units.archived_at")


if __name__ == "__main__":
    main()
