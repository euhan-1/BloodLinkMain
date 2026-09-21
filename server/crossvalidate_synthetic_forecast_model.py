"""
PHASE 4 — forecast cross-validation. Ljung-Box (Stages 4-5) never cleared for
SARIMAX(0,1,4)x(1,0,1,7)+dengue on the synthetic series (post seasonal
recalibration — see SYNTHETIC_SARIMAX_VALIDATION.md), even after the bounded
respecification attempts in Stage 5b. That means some structure is
still detectable in the residuals in principle — it does not by itself mean
the forecasts are inaccurate in practice. This checks the practical question
directly: rolling-origin (walk-forward) out-of-sample accuracy, per blood
type, against two naive baselines.

Design: expanding-window origins spaced 20 days apart over the last ~140
days of the 2-year synthetic series, each forecasting 14 days ahead (a
short-term operational horizon). Baselines use only information available
at the origin (no leakage):
  - persistence: flat-line forecast at the last training value
  - seasonal-naive: cycles through the last 7 training values (matches the
    known weekly restock structure without fitting anything)

Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA).
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from statsmodels.tsa.statespace.sarimax import SARIMAX

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}
ORDER = (0, 1, 4)
SEASONAL_ORDER = (1, 0, 1, 7)
MAXITER = 200

HORIZON = 14
ORIGIN_STEP = 20
NUM_ORIGINS = 8  # origins at n-14, n-34, ..., spanning the last ~160 days

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\crossvalidation_results.json"
)


def _load_series(blood_type: str) -> pd.Series:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT snapshot_date, units FROM synthetic_inventory_snapshots "
                "WHERE blood_type = :bt ORDER BY snapshot_date"
            ),
            {"bt": blood_type},
        ).all()
    idx = pd.DatetimeIndex([r[0] for r in rows]).to_period("D").to_timestamp()
    return pd.Series([r[1] for r in rows], index=idx, name=blood_type)


def _dengue_exog(idx: pd.DatetimeIndex) -> pd.DataFrame:
    flag = idx.month.isin(DENGUE_SEASON_MONTHS).astype(int)
    return pd.DataFrame({"dengue_season": flag}, index=idx)


def main():
    all_results = {}
    for blood_type in BLOOD_TYPES:
        endog = _load_series(blood_type)
        exog = _dengue_exog(endog.index)
        n = len(endog)

        last_origin = n - HORIZON
        origins = [last_origin - i * ORIGIN_STEP for i in range(NUM_ORIGINS)][::-1]
        origins = [o for o in origins if o > 60]  # need enough training history

        per_origin = []
        for origin in origins:
            train_endog, train_exog = endog.iloc[:origin], exog.iloc[:origin]
            test_endog, test_exog = endog.iloc[origin:origin + HORIZON], exog.iloc[origin:origin + HORIZON]

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = SARIMAX(
                    train_endog, exog=train_exog, order=ORDER, seasonal_order=SEASONAL_ORDER,
                    trend=None, enforce_stationarity=False, enforce_invertibility=False,
                ).fit(disp=False, maxiter=MAXITER)
            converged = fit.mle_retvals.get("converged", None)
            sarimax_fc = fit.get_forecast(steps=HORIZON, exog=test_exog).predicted_mean.values

            last7 = train_endog.values[-7:]
            seasonal_naive_fc = np.array([last7[(h - 1) % 7] for h in range(1, HORIZON + 1)])
            persistence_fc = np.full(HORIZON, train_endog.values[-1])

            actual = test_endog.values
            per_origin.append({
                "origin_date": endog.index[origin].strftime("%Y-%m-%d"),
                "converged": converged,
                "actual": actual.tolist(),
                "sarimax_forecast": sarimax_fc.round(2).tolist(),
                "seasonal_naive_forecast": seasonal_naive_fc.tolist(),
                "persistence_forecast": persistence_fc.tolist(),
                "sarimax_abs_err": np.abs(actual - sarimax_fc).round(3).tolist(),
                "seasonal_naive_abs_err": np.abs(actual - seasonal_naive_fc).round(3).tolist(),
                "persistence_abs_err": np.abs(actual - persistence_fc).round(3).tolist(),
            })

        def _agg(key):
            errs = np.array([o[key] for o in per_origin])  # shape (n_origins, HORIZON)
            return {
                "mae": round(float(errs.mean()), 3),
                "rmse": round(float(np.sqrt((errs ** 2).mean())), 3),
                "mae_by_step": [round(float(v), 3) for v in errs.mean(axis=0)],
            }

        all_results[blood_type] = {
            "n_origins": len(per_origin),
            "horizon": HORIZON,
            "per_origin": per_origin,
            "sarimax": _agg("sarimax_abs_err"),
            "seasonal_naive": _agg("seasonal_naive_abs_err"),
            "persistence": _agg("persistence_abs_err"),
        }

        r = all_results[blood_type]
        print(f"\n=== {blood_type}  ({r['n_origins']} origins x {HORIZON}-day horizon) ===")
        print(f"  SARIMAX{ORDER}x{SEASONAL_ORDER}+dengue:  MAE={r['sarimax']['mae']:>7.3f}  RMSE={r['sarimax']['rmse']:>7.3f}")
        print(f"  Seasonal-naive (lag-7 cycle):     MAE={r['seasonal_naive']['mae']:>7.3f}  RMSE={r['seasonal_naive']['rmse']:>7.3f}")
        print(f"  Persistence (flat last value):    MAE={r['persistence']['mae']:>7.3f}  RMSE={r['persistence']['rmse']:>7.3f}")
        skill_vs_seasonal_naive = (1 - r["sarimax"]["mae"] / r["seasonal_naive"]["mae"]) * 100
        skill_vs_persistence = (1 - r["sarimax"]["mae"] / r["persistence"]["mae"]) * 100
        print(f"  SARIMAX MAE improvement vs seasonal-naive: {skill_vs_seasonal_naive:+.1f}%")
        print(f"  SARIMAX MAE improvement vs persistence:    {skill_vs_persistence:+.1f}%")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(all_results))
    print(f"\nwrote results to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
