"""
STAGE 5 — back to identification, per the methodology's rule that a Ljung-Box
failure sends you back to re-identify rather than accepting the model.

Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA). Two parts:

1. Seasonal ACF/PACF: inspects the Stage 4 SARIMAX(0,1,4)+dengue residuals at
   multiples of lag 7 (the restock-cycle period Stage 4 traced the failure
   to), to get a data-driven starting point for the seasonal order.
2. Refits SARIMAX(0,1,4)x(P,0,Q,7)+dengue for two seasonal candidates —
   (1,0,0,7) and (1,0,1,7) — compares AIC/BIC against each other and against
   the Stage 3 non-seasonal baseline, then re-runs Ljung-Box on the winning
   seasonal candidate's residuals to check whether it actually resolves the
   Stage 4 failure.

IMPORTANT CAVEAT (for how this result gets described later): the weekly
periodicity being modeled here is a property of *this synthetic generator*
(it schedules restocks every RESTOCK_INTERVAL_RANGE=(5,9) days — see
generate_synthetic_forecast_data.py), not an empirical claim about real
blood-bank restocking or consumption cadence. Fitting a seasonal(...,7) term
demonstrates the SARIMAX methodology handles a known periodic structure
correctly; it says nothing about whether real facilities restock weekly.
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, pacf
from statsmodels.tsa.statespace.sarimax import SARIMAX

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}
BASE_ORDER = (0, 1, 4)  # Stage 3's AIC/BIC-preferred non-seasonal order (post seasonal recalibration), kept fixed here
SEASONAL_PERIOD = 7
SEASONAL_CANDIDATES = [(1, 0, 0, SEASONAL_PERIOD), (1, 0, 1, SEASONAL_PERIOD)]
SEASONAL_ACF_NLAGS = 35  # 5 multiples of 7
LJUNG_BOX_LAGS = [10, 20, 30, 40]

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\seasonal_reidentification_results.json"
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


MAXITER = 200  # statsmodels' default of 50 was not enough for the seasonal(...,7)
# candidates: verified by hand that the O+ seasonal(1,0,0,7) fit hit the default
# maxiter without converging (mle_retvals['converged']=False) and landed on an
# implausible dengue coefficient (+69.6, wrong sign, worse AIC than the properly
# converged fit at -8.36). Every fit's convergence flag is now checked explicitly
# below rather than trusting the result blindly.


def _fit(endog, exog, order, seasonal_order=None):
    # trend=None: order's d=1 here (see estimate_synthetic_forecast_models.py's
    # _fit_candidate for why a drift term isn't justified for this generator).
    kwargs = dict(order=order, trend=None, enforce_stationarity=False, enforce_invertibility=False)
    if seasonal_order is not None:
        kwargs["seasonal_order"] = seasonal_order
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = SARIMAX(endog, exog=exog, **kwargs).fit(disp=False, maxiter=MAXITER)
    return fit


def _candidate_summary(fit, order, seasonal_order=None) -> dict:
    converged = fit.mle_retvals.get("converged", None)
    return {
        "order": order,
        "seasonal_order": seasonal_order,
        "aic": round(float(fit.aic), 2),
        "bic": round(float(fit.bic), 2),
        "dengue_coef": round(float(fit.params["dengue_season"]), 4),
        "dengue_std_err": round(float(fit.bse["dengue_season"]), 4),
        "dengue_p_value": round(float(fit.pvalues["dengue_season"]), 6),
        "dengue_significant_5pct": bool(fit.pvalues["dengue_season"] < 0.05),
        "converged": converged,
    }


def _ljung_box(fit, order, seasonal_order) -> dict:
    model_df = order[0] + order[2] + (seasonal_order[0] + seasonal_order[2] if seasonal_order else 0)
    lb = acorr_ljungbox(fit.resid, lags=LJUNG_BOX_LAGS, model_df=model_df, return_df=True)
    per_lag = [
        {
            "lag": int(lag),
            "lb_stat": round(float(row.lb_stat), 3),
            "lb_pvalue": round(float(row.lb_pvalue), 5),
            "passes_5pct": bool(row.lb_pvalue > 0.05),
        }
        for lag, row in zip(LJUNG_BOX_LAGS, lb.itertuples(index=False))
    ]
    return {"per_lag": per_lag, "all_lags_pass": all(r["passes_5pct"] for r in per_lag)}


def main():
    results = {}
    for blood_type in BLOOD_TYPES:
        endog = _load_series(blood_type)
        exog = _dengue_exog(endog.index)

        # --- part 1: seasonal ACF/PACF of the Stage 4 baseline residuals ---
        base_fit = _fit(endog, exog, BASE_ORDER)
        resid_acf = acf(base_fit.resid, nlags=SEASONAL_ACF_NLAGS, fft=True)
        resid_pacf = pacf(base_fit.resid, nlags=SEASONAL_ACF_NLAGS)
        conf_bound = 1.96 / np.sqrt(len(base_fit.resid))
        seasonal_lags = list(range(SEASONAL_PERIOD, SEASONAL_ACF_NLAGS + 1, SEASONAL_PERIOD))
        seasonal_acf_pacf = [
            {
                "lag": lag,
                "acf": round(float(resid_acf[lag]), 4),
                "pacf": round(float(resid_pacf[lag]), 4),
            }
            for lag in seasonal_lags
        ]

        # --- part 2: refit with seasonal candidates ---
        candidates = [_candidate_summary(base_fit, BASE_ORDER, None)]
        fits_by_order = {(BASE_ORDER, None): base_fit}
        for seasonal_order in SEASONAL_CANDIDATES:
            fit = _fit(endog, exog, BASE_ORDER, seasonal_order)
            fits_by_order[(BASE_ORDER, seasonal_order)] = fit
            candidates.append(_candidate_summary(fit, BASE_ORDER, seasonal_order))

        seasonal_candidates_only = [c for c in candidates if c["seasonal_order"] is not None]
        best = min(seasonal_candidates_only, key=lambda c: c["aic"])
        best_fit = fits_by_order[(BASE_ORDER, tuple(best["seasonal_order"]))]
        ljung_box = _ljung_box(best_fit, BASE_ORDER, best["seasonal_order"])

        results[blood_type] = {
            "conf_bound": round(float(conf_bound), 5),
            "seasonal_acf_pacf": seasonal_acf_pacf,
            "candidates": candidates,
            "best_seasonal_order": best["seasonal_order"],
            "ljung_box_on_best": ljung_box,
        }

        print(f"\n=== {blood_type} ===")
        print("  seasonal ACF/PACF of (1,0,1)+dengue residuals, at multiples of 7:")
        for row in seasonal_acf_pacf:
            print(f"    lag {row['lag']:>2}: acf={row['acf']:>8.4f}  pacf={row['pacf']:>8.4f}")
        print("  candidates:")
        for c in candidates:
            so = c["seasonal_order"] or "none"
            conv_flag = "" if c["converged"] else "  [DID NOT CONVERGE]"
            print(
                f"    order={c['order']} seasonal={so}  AIC={c['aic']:>10.2f}  BIC={c['bic']:>10.2f}  "
                f"dengue_coef={c['dengue_coef']:>8.4f}  p={c['dengue_p_value']:.5f}  sig={c['dengue_significant_5pct']}{conv_flag}"
            )
        print(f"  -> best seasonal candidate by AIC: {best['seasonal_order']}")
        print(f"  Ljung-Box on best seasonal candidate's residuals:")
        for r in ljung_box["per_lag"]:
            flag = "PASS" if r["passes_5pct"] else "FAIL"
            print(f"    lag={r['lag']:>3}  p={r['lb_pvalue']:.5f}  {flag}")
        print(f"  -> {'all lags pass' if ljung_box['all_lags_pass'] else 'at least one lag still fails'}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results))
    print(f"\nwrote results to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
