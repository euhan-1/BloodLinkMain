"""Applies schema_transfer_integrity.sql. NOT RUN YET. DATABASE_URL in server/.env is production: take a backup first.

Idempotent: safe to re-run. Run from server/ with the venv: .venv\\Scripts\\python.exe migrate_transfer_integrity.py
Apply it BEFORE deploying the backend that reads requests.component; an older backend ignores the column.
"""
from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_transfer_integrity.sql").read_text(encoding="utf-8")

    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        print("transfer integrity schema ensured")


if __name__ == "__main__":
    main()
