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
        conn.execute(text("ALTER TABLE users ALTER COLUMN facility_id DROP NOT NULL"))
        print("users.facility_id is now nullable")

        if not _has_column(conn, "facilities", "is_active"):
            conn.execute(text("ALTER TABLE facilities ADD COLUMN is_active boolean NOT NULL DEFAULT true"))
            print("added facilities.is_active (default true)")
        else:
            print("facilities.is_active already exists, skipping")


if __name__ == "__main__":
    main()
