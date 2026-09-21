"""
STAGE 3 — Box-Jenkins parameter estimation pass on the synthetic dataset.
Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA — see
generate_synthetic_forecast_data.py). Fits candidate SARIMAX(p,0,q) models
per blood type with a dengue-season exogenous regressor, per the Stage 2
finding that d=0 for every type. Does not do residual diagnostics — just
fits candidates and compares AIC/BIC, per explicit instruction.

Exogenous variable: a binary dengue-season indicator built from
DENGUE_SEASON_MONTHS (calendar months 6-10), matching the definition already
used in main.py's shortage-alert logic — not the smooth cosine curve used
internally by the synthetic generator. This mirrors what a real deployment
would actually have available (a calendar rule), not the generator's
internal parameters.

Candidates: (p, d, q) = (0, 1, 1), (0, 1, 4), and (1, 1, 1) for every blood
type. Re-identified after the seasonal-calibration pass (see
SYNTHETIC_SARIMAX_VALIDATION.md, "Seasonal calibration"): with the fixed
seasonal shape, the raw series is no longer stationary at d=0 for any blood
type (Stage 2 ADF, all 8 types) — a real, expected consequence of the
seasonal excursion now being deeper and more sustained, not a defect. At
d=1, ACF cuts off sharply after lag ~4 while PACF decays gradually across
many lags for every type — a classic MA signature, the opposite of the
previous (buggy-data) AR(1) signature this file's candidates used to target.
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
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}  # matches main.py's DENGUE_SEASON_MONTHS
CANDIDATE_ORDERS = [(0, 1, 1), (0, 1, 4), (1, 1, 1)]

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\estimation_results.json"
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


def _fit_candidate(endog: pd.Series, exog: pd.DataFrame, order: tuple[int, int, int]) -> dict:
    # trend='c' only when d=0: without an intercept there, the AR term gets
    # pulled toward a near-unit-root value to compensate for the missing
    # baseline level, corrupting the dengue_season coefficient (verified by
    # hand on the original d=0 candidates: no-intercept fit put ar.L1 at 0.98
    # vs 0.79 once the intercept was added, and flipped the dengue
    # coefficient's sign on some types). For d>=1 (every candidate here,
    # post seasonal-recalibration — see module docstring) a constant instead
    # means a linear drift in the undifferenced series, which this generator
    # has none of by design (bounded, mean-reverting, seasonal) — adding one
    # would be an unjustified assumption, not a fix for a missing intercept.
    trend = "c" if order[1] == 0 else None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = SARIMAX(
            endog, exog=exog, order=order, trend=trend,
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fit = model.fit(disp=False)
        convergence_warnings = [
            str(w.message) for w in caught if w.category.__name__ != "DeprecationWarning"
        ]

    return {
        "order": order,
        "aic": round(float(fit.aic), 2),
        "bic": round(float(fit.bic), 2),
        "llf": round(float(fit.llf), 2),
        "dengue_coef": round(float(fit.params["dengue_season"]), 4),
        "dengue_std_err": round(float(fit.bse["dengue_season"]), 4),
        "dengue_p_value": round(float(fit.pvalues["dengue_season"]), 6),
        "dengue_significant_5pct": bool(fit.pvalues["dengue_season"] < 0.05),
        "converged": fit.mle_retvals.get("converged", None) if hasattr(fit, "mle_retvals") else None,
        "convergence_warnings": convergence_warnings,
        "all_params": {name: round(float(val), 4) for name, val in fit.params.items()},
    }


def main():
    results = {}
    for blood_type in BLOOD_TYPES:
        endog = _load_series(blood_type)
        exog = _dengue_exog(endog.index)

        candidates = []
        for order in CANDIDATE_ORDERS:
            result = _fit_candidate(endog, exog, order)
            candidates.append(result)

        best_aic = min(candidates, key=lambda c: c["aic"])
        best_bic = min(candidates, key=lambda c: c["bic"])
        results[blood_type] = {
            "candidates": candidates,
            "best_by_aic": best_aic["order"],
            "best_by_bic": best_bic["order"],
        }

        print(f"\n=== {blood_type} ===")
        for c in candidates:
            warn_flag = f"  [{len(c['convergence_warnings'])} warning(s)]" if c["convergence_warnings"] else ""
            print(
                f"  order={c['order']}  AIC={c['aic']:>10.2f}  BIC={c['bic']:>10.2f}  "
                f"dengue_coef={c['dengue_coef']:>8.4f}  se={c['dengue_std_err']:.4f}  "
                f"p={c['dengue_p_value']:.6f}  sig@5%={c['dengue_significant_5pct']}{warn_flag}"
            )
        print(f"  -> best by AIC: {best_aic['order']}   best by BIC: {best_bic['order']}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results, default=str))
    print(f"\nwrote estimation results to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
