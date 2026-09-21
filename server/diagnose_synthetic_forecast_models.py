"""
STAGE 4 — Box-Jenkins diagnostic checking pass on the synthetic dataset.
Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA). Fits the
SARIMAX(0,1,4) + dengue-season model per blood type (the Stage 3 AIC/BIC
-preferred candidate, uniformly across all 8 types, post seasonal
recalibration — see SYNTHETIC_SARIMAX_VALIDATION.md) and runs the Ljung-Box
test on the residuals: confirms nothing systematic (autocorrelation the
model failed to capture) is left over. Nothing beyond Ljung-Box here, per
explicit scope.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf
from statsmodels.tsa.statespace.sarimax import SARIMAX

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}
ORDER = (0, 1, 4)  # Stage 3's AIC/BIC-preferred candidate, uniformly across all 8 types
LJUNG_BOX_LAGS = [10, 20, 30, 40]  # same range as Stage 2's ACF/PACF nlags for continuity
RESID_ACF_NLAGS = 21  # enough to show the lag ~7/14 restock-cycle bump alongside lag 1-6

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\diagnostics_results.json"
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
    results = {}
    for blood_type in BLOOD_TYPES:
        endog = _load_series(blood_type)
        exog = _dengue_exog(endog.index)

        model = SARIMAX(
            endog, exog=exog, order=ORDER, trend=None,  # d=1: no drift term — see estimate_synthetic_forecast_models.py's _fit_candidate for why
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fit = model.fit(disp=False)

        # model_df subtracts the estimated AR+MA parameters (p+q=4 here) from the
        # Ljung-Box degrees of freedom, per the standard Box-Jenkins recommendation
        # for testing residuals of a fitted ARMA-type model rather than raw data.
        lb = acorr_ljungbox(fit.resid, lags=LJUNG_BOX_LAGS, model_df=sum(ORDER[::2]), return_df=True)

        per_lag = [
            {
                "lag": int(lag),
                "lb_stat": round(float(row.lb_stat), 3),
                "lb_pvalue": round(float(row.lb_pvalue), 5),
                "passes_5pct": bool(row.lb_pvalue > 0.05),
            }
            for lag, row in zip(LJUNG_BOX_LAGS, lb.itertuples(index=False))
        ]
        all_pass = all(r["passes_5pct"] for r in per_lag)

        resid_acf = acf(fit.resid, nlags=RESID_ACF_NLAGS, fft=True)
        resid_conf_bound = 1.96 / np.sqrt(len(fit.resid))

        results[blood_type] = {
            "order": ORDER,
            "per_lag": per_lag,
            "all_lags_pass": all_pass,
            "resid_acf": [round(float(v), 5) for v in resid_acf],
            "resid_conf_bound": round(float(resid_conf_bound), 5),
        }

        print(f"\n=== {blood_type}  SARIMAX{ORDER} ===")
        for r in per_lag:
            flag = "PASS (white noise)" if r["passes_5pct"] else "FAIL (autocorrelation remains)"
            print(f"  lag={r['lag']:>3}  LB stat={r['lb_stat']:>8.3f}  p={r['lb_pvalue']:.5f}  {flag}")
        print(f"  -> {'all lags pass' if all_pass else 'at least one lag fails'}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results))
    print(f"\nwrote diagnostics results to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
