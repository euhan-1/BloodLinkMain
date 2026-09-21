from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_blast_replies.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("blast_replies table ready (no seed data)")


if __name__ == "__main__":
    main()
