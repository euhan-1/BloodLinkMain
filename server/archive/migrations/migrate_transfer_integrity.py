"""Applies schema_transfer_integrity.sql (the SQL file beside this one). ALREADY APPLIED to production: `requests.component`, the
`request_units` ledger and the eight stamp/actor columns exist there. Archived; read README.md in this folder before running anything.

Idempotent, so a re-run is harmless, but there is no reason to. It was run from server/ with the venv: .venv\\Scripts\\python.exe migrate_transfer_integrity.py
It had to be applied BEFORE deploying the backend that reads requests.component; an older backend ignores the column.
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
