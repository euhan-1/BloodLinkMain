from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_password_reset.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("password_reset_requests table ready")


if __name__ == "__main__":
    main()
