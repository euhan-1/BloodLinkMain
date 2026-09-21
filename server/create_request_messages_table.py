from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_request_messages.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("request_messages table ready (no seed data — starts empty, populated by real user actions)")


if __name__ == "__main__":
    main()
