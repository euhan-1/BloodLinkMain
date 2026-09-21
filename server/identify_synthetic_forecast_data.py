"""
STAGE 2 — Box-Jenkins identification pass on the synthetic dataset.
Reads from `synthetic_inventory_snapshots` (SYNTHETIC TEST DATA — see
generate_synthetic_forecast_data.py). No modeling here, per explicit
instruction: this only runs the ADF stationarity test, applies differencing
until stationary, and computes ACF/PACF on the resulting series so the next
stage has a data-driven starting point for (p, d, q). Does not fit SARIMAX.

Output: prints ADF results before/after differencing for all 8 blood types,
and writes ACF/PACF + raw/differenced series data to a JSON file in the
scratchpad for the verification chart.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from statsmodels.tsa.stattools import acf, adfuller, pacf

from database import engine

BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
ADF_ALPHA = 0.05
MAX_DIFF_ORDER = 2
ACF_PACF_NLAGS = 40

OUTPUT_JSON = Path(
    r"C:\Users\euhan\AppData\Local\Temp\claude\d--BloodLink-Web-Application-Design"
    r"\26edace0-4c1a-4c1f-bc8f-d8722d35ef99\scratchpad\identification_results.json"
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
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.Series([r[1] for r in rows], index=idx, name=blood_type)


def _run_adf(series: pd.Series) -> dict:
    stat, pvalue, used_lag, nobs, crit_values, _ = adfuller(series.values, autolag="AIC")
    return {
        "statistic": round(float(stat), 4),
        "p_value": round(float(pvalue), 5),
        "used_lag": int(used_lag),
        "n_obs": int(nobs),
        "critical_values": {k: round(float(v), 4) for k, v in crit_values.items()},
        "stationary_at_5pct": bool(pvalue < ADF_ALPHA),
    }


def _difference_until_stationary(series: pd.Series) -> tuple[pd.Series, int, list[dict]]:
    """Applies first differencing repeatedly (up to MAX_DIFF_ORDER) until the ADF
    test rejects the unit-root null at ADF_ALPHA. Returns the resulting series,
    the order d actually used, and the ADF result at every order tried (0..d)."""
    current = series
    adf_history = [_run_adf(current)]
    d = 0
    while not adf_history[-1]["stationary_at_5pct"] and d < MAX_DIFF_ORDER:
        current = current.diff().dropna()
        d += 1
        adf_history.append(_run_adf(current))
    return current, d, adf_history


def main():
    results = {}
    for blood_type in BLOOD_TYPES:
        raw = _load_series(blood_type)
        stationary_series, d, adf_history = _difference_until_stationary(raw)

        acf_vals = acf(stationary_series.values, nlags=ACF_PACF_NLAGS, fft=True)
        pacf_vals = pacf(stationary_series.values, nlags=ACF_PACF_NLAGS)
        n = len(stationary_series)
        conf_bound = 1.96 / np.sqrt(n)  # standard large-sample 95% significance band

        results[blood_type] = {
            "d": d,
            "adf_by_order": adf_history,  # index 0 = raw, index d = final differenced series used below
            "n_after_differencing": n,
            "conf_bound": round(float(conf_bound), 5),
            "acf": [round(float(v), 5) for v in acf_vals],
            "pacf": [round(float(v), 5) for v in pacf_vals],
            "raw_series": [[ts.strftime("%Y-%m-%d"), float(v)] for ts, v in raw.items()],
            "differenced_series": [
                [ts.strftime("%Y-%m-%d"), float(v)] for ts, v in stationary_series.items()
            ],
        }

        print(f"\n=== {blood_type} ===")
        for order, adf_result in enumerate(adf_history):
            tag = "raw" if order == 0 else f"diff order {order}"
            print(
                f"  [{tag:<14}] ADF stat={adf_result['statistic']:>8.4f}  "
                f"p={adf_result['p_value']:.5f}  "
                f"stationary@5%={adf_result['stationary_at_5pct']}"
            )
        print(f"  -> d = {d} (order used for ACF/PACF below)")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results))
    print(f"\nwrote identification results to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
