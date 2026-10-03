from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_upload_history.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        print("upload_history table + index ensured")


if __name__ == "__main__":
    main()
