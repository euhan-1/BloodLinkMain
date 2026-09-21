from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_donors.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("donors table ready (no seed data — real rosters only, uploaded via CSV)")


if __name__ == "__main__":
    main()
