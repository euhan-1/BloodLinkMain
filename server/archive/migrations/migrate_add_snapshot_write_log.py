from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_snapshot_write_log.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        print("inventory_snapshot_write_log table ensured")


if __name__ == "__main__":
    main()
