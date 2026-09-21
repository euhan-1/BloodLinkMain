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
        if not _has_column(conn, "requests", "supplier_confirmed_at"):
            conn.execute(text("ALTER TABLE requests ADD COLUMN supplier_confirmed_at timestamptz"))
            print("added requests.supplier_confirmed_at")
        else:
            print("requests.supplier_confirmed_at already exists, skipping")

        if not _has_column(conn, "requests", "requester_confirmed_at"):
            conn.execute(text("ALTER TABLE requests ADD COLUMN requester_confirmed_at timestamptz"))
            print("added requests.requester_confirmed_at")
        else:
            print("requests.requester_confirmed_at already exists, skipping")

        if not _has_column(conn, "blood_units", "reserved_for_request_id"):
            conn.execute(
                text("ALTER TABLE blood_units ADD COLUMN reserved_for_request_id bigint REFERENCES requests(id)")
            )
            print("added blood_units.reserved_for_request_id")
        else:
            print("blood_units.reserved_for_request_id already exists, skipping")


if __name__ == "__main__":
    main()
