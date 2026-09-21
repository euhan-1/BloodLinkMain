"""Creates facility_forecast_cache — the real, per-facility SARIMAX forecast
cache GET /forecast reads/writes once a facility clears
SARIMAX_MIN_DAYS_REQUIRED days of real history (see main.py's
_get_or_fit_cached_sarimax). Idempotent: CREATE TABLE IF NOT EXISTS, safe to
re-run.

Run manually, once, before deploying the real-SARIMAX-on-real-data forecast
path (like the other migrate_*.py scripts in this project).
"""

from pathlib import Path

from sqlalchemy import text

from database import engine


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_facility_forecast_cache.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("facility_forecast_cache ready (created if it didn't already exist)")


if __name__ == "__main__":
    main()
