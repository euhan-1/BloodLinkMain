"""
STAGE 5b — one more bounded respecification attempt before giving up on
clearing the Ljung-Box failure. Two alternatives to the Stage 5 best model
(SARIMAX(0,1,4)x(1,0,1,7)+dengue — post seasonal recalibration, see
SYNTHETIC_SARIMAX_VALIDATION.md):

  A. SARIMAX(0,1,4)x(1,0,2,7)+dengue  — higher-order seasonal MA
  B. SARIMAX(1,1,4)+dengue            — added non-seasonal AR term,
                                          no seasonal term at all

Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA). Reuses the
Stage 5 convergence-checking pattern (maxiter=200, explicit check of
mle_retvals['converged']) after the earlier convergence bug there.
"""

import json
import warnings
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.statespace.sarimax import SARIMAX

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}
MAXITER = 200
LJUNG_BOX_LAGS = [10, 20, 30, 40]

CANDIDATES = [
    {"label": "seasonal_ma2", "order": (0, 1, 4), "seasonal_order": (1, 0, 2, 7)},
    {"label": "nonseasonal_ar1", "order": (1, 1, 4), "seasonal_order": None},
]

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\bounded_respecification_results.json"
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


def _fit(endog, exog, order, seasonal_order):
    # trend=None: both candidates have d=1 (see estimate_synthetic_forecast_models.py's
    # _fit_candidate for why a drift term isn't justified for this generator).
    kwargs = dict(order=order, trend=None, enforce_stationarity=False, enforce_invertibility=False)
    if seasonal_order is not None:
        kwargs["seasonal_order"] = seasonal_order
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = SARIMAX(endog, exog=exog, **kwargs).fit(disp=False, maxiter=MAXITER)
    return fit


def _ljung_box(fit, order, seasonal_order):
    model_df = order[0] + order[2] + (seasonal_order[0] + seasonal_order[2] if seasonal_order else 0)
    lb = acorr_ljungbox(fit.resid, lags=LJUNG_BOX_LAGS, model_df=model_df, return_df=True)
    per_lag = [
        {
            "lag": int(lag),
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

        print(f"\n=== {blood_type} ===")
        type_results = []
        for cand in CANDIDATES:
            fit = _fit(endog, exog, cand["order"], cand["seasonal_order"])
            converged = fit.mle_retvals.get("converged", None)
            lb = _ljung_box(fit, cand["order"], cand["seasonal_order"])

            summary = {
                "label": cand["label"],
                "order": cand["order"],
                "seasonal_order": cand["seasonal_order"],
                "aic": round(float(fit.aic), 2),
                "bic": round(float(fit.bic), 2),
                "dengue_coef": round(float(fit.params["dengue_season"]), 4),
                "dengue_p_value": round(float(fit.pvalues["dengue_season"]), 6),
                "dengue_significant_5pct": bool(fit.pvalues["dengue_season"] < 0.05),
                "converged": converged,
                "ljung_box": lb,
            }
            type_results.append(summary)

            so = cand["seasonal_order"] or "none"
            conv_flag = "" if converged else "  [DID NOT CONVERGE]"
            print(
                f"  {cand['label']:<18} order={cand['order']} seasonal={so}  "
                f"AIC={summary['aic']:>10.2f}  BIC={summary['bic']:>10.2f}  "
                f"dengue_coef={summary['dengue_coef']:>8.4f}  p={summary['dengue_p_value']:.5f}{conv_flag}"
            )
            for r in lb["per_lag"]:
                flag = "PASS" if r["passes_5pct"] else "FAIL"
                print(f"      LB lag={r['lag']:>3}  p={r['lb_pvalue']:.5f}  {flag}")
            print(f"      -> {'all lags pass' if lb['all_lags_pass'] else 'at least one lag fails'}")

        results[blood_type] = type_results

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results))
    print(f"\nwrote results to {OUTPUT_JSON}")

    any_pass = any(c["ljung_box"]["all_lags_pass"] for t in results.values() for c in t)
    print(f"\nAny candidate cleared Ljung-Box for any blood type: {any_pass}")


if __name__ == "__main__":
    main()
