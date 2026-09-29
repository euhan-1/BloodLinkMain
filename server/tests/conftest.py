"""Runs the suite against a scratch local Postgres, never production.

Requires TEST_DATABASE_URL pointing at localhost. Before any test module
imports database.py, DATABASE_URL is overridden with it (load_dotenv never
overrides an existing variable), and the public schema is rebuilt from
tests/schema.sql, a `pg_dump --schema-only` of production. Every session
starts from empty tables.

Regenerate schema.sql after a migration (pg_dump must be >= the server's major version):
    pg_dump --schema-only --schema=public --no-owner --no-privileges "<prod url, no +psycopg2>" > tests/schema.sql
"""
import os
import pathlib

import pytest
from sqlalchemy import create_engine, make_url

_url = os.environ.get("TEST_DATABASE_URL")
if not _url:
    pytest.exit("TEST_DATABASE_URL is not set. Point it at a local scratch Postgres; the suite never runs against production.", 4)
if make_url(_url).host not in ("localhost", "127.0.0.1", "::1"):
    pytest.exit(f"TEST_DATABASE_URL host {make_url(_url).host!r} is not local; refusing to drop its schema.", 4)
os.environ["DATABASE_URL"] = _url

# psql meta-commands (\restrict, \unrestrict in pg_dump >= 17.6) aren't SQL.
_schema = "\n".join(
    line for line in (pathlib.Path(__file__).parent / "schema.sql").read_text(encoding="utf-8").splitlines()
    if not line.startswith("\\")
)
_engine = create_engine(_url)
_raw = _engine.raw_connection()  # raw cursor: no parameter binding, so '%' in the dump is literal
try:
    with _raw.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE")  # the dump's own CREATE SCHEMA public recreates it
        cur.execute(_schema)
    _raw.commit()
finally:
    _raw.close()
    _engine.dispose()
