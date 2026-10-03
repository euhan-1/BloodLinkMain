from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_users.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("users table ready (no seed data — real accounts only)")


if __name__ == "__main__":
    main()
