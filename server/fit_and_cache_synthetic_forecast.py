"""
Fits SARIMAX(0,1,4)x(1,0,1,7)+dengue — the model validated in
SYNTHETIC_SARIMAX_VALIDATION.md — on the full synthetic dataset per blood
type, forecasts forward, and caches the result in `synthetic_forecast_cache`
for /forecast to read at request time.

Run manually (like the other seed/generate scripts in this project) whenever
the cache needs extending — not on a schedule. Caches CACHE_HORIZON_DAYS
ahead of today so it stays valid for a long stretch without needing to be
re-run constantly. Safe to re-run: clears and regenerates from scratch.
"""

import json
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from statsmodels.tsa.statespace.sarimax import SARIMAX

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}  # must match main.py's DENGUE_SEASON_MONTHS
ORDER = (0, 1, 4)
SEASONAL_ORDER = (1, 0, 1, 7)
MODEL_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"
MAXITER = 200

CACHE_HORIZON_DAYS = 400  # forecast this far past today so the cache stays valid for a long stretch


def _load_series(blood_type: str) -> pd.Series:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT snapshot_date, units FROM synthetic_inventory_snapshots "
                "WHERE blood_type = :bt ORDER BY snapshot_date"
            ),
            {"bt": blood_type},
        ).all()
    if not rows:
        raise RuntimeError("synthetic_inventory_snapshots is empty — run generate_synthetic_forecast_data.py first")
    idx = pd.DatetimeIndex([r[0] for r in rows]).to_period("D").to_timestamp()
    return pd.Series([r[1] for r in rows], index=idx, name=blood_type)


def _dengue_flag(idx: pd.DatetimeIndex) -> pd.DataFrame:
    flag = idx.month.isin(DENGUE_SEASON_MONTHS).astype(int)
    return pd.DataFrame({"dengue_season": flag}, index=idx)


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_synthetic_forecast_cache.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))

    target_end = date.today() + timedelta(days=CACHE_HORIZON_DAYS)
    all_rows = []

    for blood_type in BLOOD_TYPES:
        endog = _load_series(blood_type)
        exog = _dengue_flag(endog.index)
        last_train_date = endog.index[-1].date()

        steps = (target_end - last_train_date).days
        if steps <= 0:
            raise RuntimeError(f"synthetic series for {blood_type} already extends past target_end")

        future_dates = pd.date_range(last_train_date + timedelta(days=1), periods=steps, freq="D")
        future_exog = _dengue_flag(pd.DatetimeIndex(future_dates))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(
                endog, exog=exog, order=ORDER, seasonal_order=SEASONAL_ORDER, trend=None,
                enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False, maxiter=MAXITER)

        converged = fit.mle_retvals.get("converged", None)
        if not converged:
            raise RuntimeError(f"{blood_type} fit did not converge (mle_retvals={fit.mle_retvals}) — refusing to cache an unverified forecast")

        forecast = fit.get_forecast(steps=steps, exog=future_exog).predicted_mean
        for forecast_date, value in zip(future_dates, forecast.values):
            all_rows.append({
                "blood_type": blood_type,
                "forecast_date": forecast_date.date(),
                "forecast_units": max(0.0, round(float(value), 2)),
                "model_order": MODEL_LABEL,
            })
        print(f"{blood_type}: cached {steps} days ({last_train_date + timedelta(days=1)} to {target_end}), converged={converged}")

    with engine.begin() as conn:
        deleted = conn.execute(text("DELETE FROM synthetic_forecast_cache")).rowcount
        conn.execute(
            text(
                """
                INSERT INTO synthetic_forecast_cache (blood_type, forecast_date, forecast_units, model_order)
                VALUES (:blood_type, :forecast_date, :forecast_units, :model_order)
                """
            ),
            all_rows,
        )

    print(f"\ncleared {deleted} old cached rows")
    print(f"cached {len(all_rows)} forecast rows ({len(BLOOD_TYPES)} types through {target_end})")


if __name__ == "__main__":
    main()
