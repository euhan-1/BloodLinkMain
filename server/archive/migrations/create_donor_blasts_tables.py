from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_donor_blasts.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("blasts and blast_messages tables ready (no seed data)")


if __name__ == "__main__":
    main()
