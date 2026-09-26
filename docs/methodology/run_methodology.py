"""Box-Jenkins analysis of the 180-day Northside demonstration series.

GENERATED DEMONSTRATION DATA, not real blood bank records. Every number in
docs/methodology/PHASE_*.md comes from this script's output (results.json).
Run from the repo root:
    server\\.venv\\Scripts\\python.exe docs\\methodology\\run_methodology.py

The model spec mirrors main._fit_sarimax_facility_forecast exactly:
SARIMAX(endog, exog=dengue_season, order, seasonal_order, trend=None,
enforce_stationarity=False, enforce_invertibility=False).fit(maxiter=200),
with exog = main.dengue_season_index (the LIVE function, binary Jun-Oct).
"""
import csv, json, sys, time, warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, adfuller, pacf
from statsmodels.tsa.statespace.sarimax import SARIMAX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "server"))
import main as app  # live constants + dengue_season_index

warnings.simplefilter("ignore")
ORDER, SORDER = app.FACILITY_SARIMAX_ORDER, app.FACILITY_SARIMAX_SEASONAL_ORDER
MAXITER, HOLDOUT = app.FACILITY_SARIMAX_MAXITER, 30
ALTS = {  # all with the same dengue exog and the same fit settings
    "A (0,1,1)x(0,0,0,7)": ((0, 1, 1), (0, 0, 0, 7)),
    "B (0,1,4)x(0,0,0,7)": ((0, 1, 4), (0, 0, 0, 7)),
    "C (0,1,1)x(1,0,1,7)": ((0, 1, 1), (1, 0, 1, 7)),
    "D (1,1,1)x(1,0,1,7)": ((1, 1, 1), (1, 0, 1, 7)),
    "E (0,1,2)x(1,0,1,7)": ((0, 1, 2), (1, 0, 1, 7)),
}

df = pd.DataFrame(list(csv.DictReader(open(HERE / "northside_180d_history.csv"))))
df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
df["units"] = df["units"].astype(float)
wide = df.pivot(index="snapshot_date", columns="blood_type", values="units").sort_index()
assert len(wide) == 180 and not wide.isna().any().any()
assert (wide.index == pd.date_range(wide.index[0], periods=180, freq="D")).all(), "gaps in dates"
TYPES = list(wide.columns)
exog_all = pd.DataFrame({"dengue_season": [app.dengue_season_index(d.date()) for d in wide.index]}, index=wide.index)


def fit(y, x, order, sorder, exog=True):
    m = SARIMAX(y, exog=x if exog else None, order=order, seasonal_order=sorder, trend=None,
                enforce_stationarity=False, enforce_invertibility=False)
    return m.fit(disp=False, maxiter=MAXITER)


out = {"meta": {"days": 180, "first": str(wide.index[0].date()), "last": str(wide.index[-1].date()),
                "order": ORDER, "seasonal_order": SORDER, "types": TYPES}}

# data-shape check: is there a binary June-1 step?
jun1 = pd.Timestamp("2026-06-01")
out["step_check"] = {
    t: {"mean_7d_before_jun1": float(wide[t][jun1 - pd.Timedelta(days=7):jun1 - pd.Timedelta(days=1)].mean()),
        "mean_7d_after_jun1": float(wide[t][jun1:jun1 + pd.Timedelta(days=6)].mean()),
        "monthly_mean": {k: round(float(v), 1) for k, v in wide[t].groupby(wide.index.strftime("%Y-%m")).mean().items()}}
    for t in TYPES}

# ---------------- Phase 1 ----------------
p1 = {}
for t in TYPES:
    y = wide[t]
    r = {}
    a = adfuller(y, autolag="AIC")
    r["adf_raw"] = {"stat": a[0], "p": a[1], "lags": a[2], "verdict": "stationary" if a[1] < .05 else "non-stationary"}
    d, s = 0, y.copy()
    while adfuller(s, autolag="AIC")[1] >= .05 and d < 2:
        s = s.diff().dropna()
        d += 1
    ad = adfuller(s, autolag="AIC")
    r["min_d_for_adf"] = d
    r["adf_after_min_d"] = {"stat": ad[0], "p": ad[1]}
    d1 = y.diff().dropna()
    a1 = adfuller(d1, autolag="AIC")
    r["adf_d1"] = {"stat": a1[0], "p": a1[1], "verdict": "stationary" if a1[1] < .05 else "non-stationary"}
    bound = 1.96 / np.sqrt(len(d1))
    ac = acf(d1, nlags=28, fft=False)
    pc = pacf(d1, nlags=28, method="ywm")
    r["bound"] = bound
    r["acf"] = [float(v) for v in ac[1:]]
    r["pacf"] = [float(v) for v in pc[1:]]
    r["acf_sig_lags"] = [i for i in range(1, 29) if abs(ac[i]) > bound]
    r["pacf_sig_lags"] = [i for i in range(1, 29) if abs(pc[i]) > bound]

    def lead(sig):
        k = 0
        while (k + 1) in sig:
            k += 1
        return k
    r["q_suggest_leading_sig_acf"] = lead(r["acf_sig_lags"])
    r["p_suggest_leading_sig_pacf"] = lead(r["pacf_sig_lags"])
    r["seasonal_lags_sig_acf"] = [l for l in (7, 14, 21, 28) if l in r["acf_sig_lags"]]
    r["seasonal_lags_sig_pacf"] = [l for l in (7, 14, 21, 28) if l in r["pacf_sig_lags"]]
    p1[t] = r
out["phase1"] = p1
print("phase1 done", flush=True)

# ---------------- Phase 2 + 3 (full sample) ----------------
p2, p3 = {}, {}
for t in TYPES:
    y, x = wide[t], exog_all
    t0 = time.time()
    f = fit(y, x, ORDER, SORDER)
    r = {"converged": bool(f.mle_retvals.get("converged", False)), "aic": f.aic, "bic": f.bic, "llf": f.llf, "nobs": f.nobs,
         "params": {k: {"coef": float(f.params[k]), "se": float(f.bse[k]), "z": float(f.tvalues[k]), "p": float(f.pvalues[k])}
                    for k in f.params.index}}
    nx = fit(y, None, ORDER, SORDER, exog=False)
    r["no_exog"] = {"aic": nx.aic, "bic": nx.bic, "converged": bool(nx.mle_retvals.get("converged", False))}
    r["alts"] = {}
    for name, (o, so) in ALTS.items():
        g = fit(y, x, o, so)
        r["alts"][name] = {"aic": g.aic, "bic": g.bic, "converged": bool(g.mle_retvals.get("converged", False)),
                           "dengue_p": float(g.pvalues["dengue_season"])}
    p2[t] = r
    # diagnostics on the fixed-order fit
    burn = max(int(getattr(f, "loglikelihood_burn", 0)), 1)
    resid = np.asarray(f.standardized_forecasts_error[0])[burn:]
    resid = resid[np.isfinite(resid)]
    lb = {}
    for lag in (7, 14, 21, 28):
        res = acorr_ljungbox(resid, lags=[lag], model_df=0)
        lb[lag] = {"stat": float(res["lb_stat"].iloc[0]), "p": float(res["lb_pvalue"].iloc[0])}
    jb = f.test_normality("jarquebera")[0]
    bv = f.test_heteroskedasticity("breakvar")[0]
    bd = 1.96 / np.sqrt(len(resid))
    ra = acf(resid, nlags=28, fft=False)
    p3[t] = {"n_resid": len(resid), "burn": burn, "ljungbox": lb,
             "jb": {"stat": float(jb[0]), "p": float(jb[1]), "skew": float(jb[2]), "kurt": float(jb[3])},
             "breakvar": {"stat": float(bv[0]), "p": float(bv[1])},
             "resid_acf_bound": bd, "resid_acf_outside": [i for i in range(1, 29) if abs(ra[i]) > bd],
             "resid_acf": [float(v) for v in ra[1:]]}
    print("fit", t, f"{time.time() - t0:.1f}s", flush=True)
out["phase2"], out["phase3"] = p2, p3

# ---------------- Phase 4 holdout ----------------
p4 = {}
for t in TYPES:
    y, x = wide[t], exog_all
    ytr, yte = y.iloc[:-HOLDOUT], y.iloc[-HOLDOUT:]
    f = fit(ytr, x.iloc[:-HOLDOUT], ORDER, SORDER)
    fc = f.get_forecast(steps=HOLDOUT, exog=x.iloc[-HOLDOUT:])
    pt = fc.predicted_mean.values
    ci = np.asarray(fc.conf_int(alpha=0.05))
    act = yte.values
    naive = np.full(HOLDOUT, ytr.iloc[-1])
    snaive = np.array([ytr.iloc[-7 + (i % 7)] for i in range(HOLDOUT)])

    def m(p):
        e = act - p
        return {"mape": float(np.mean(np.abs(e) / act) * 100), "rmse": float(np.sqrt(np.mean(e ** 2))), "mae": float(np.mean(np.abs(e)))}
    inside = (act >= ci[:, 0]) & (act <= ci[:, 1])
    width = ci[:, 1] - ci[:, 0]
    # live-path parity: the real production function on the same training series
    daily = [(d.date(), int(v)) for d, v in ytr.items()]
    live = app._fit_sarimax_facility_forecast(daily, ytr.index[-1].date())
    parity = None
    if live:
        parity = {str(c): {"live": live["checkpoints"][c]["units"], "mine": int(round(max(0, pt[c - 1])))}
                  for c in app.FORECAST_CHECKPOINTS if c > 0}
    p4[t] = {"sarimax": m(pt), "naive_last": m(naive), "seasonal_naive_7": m(snaive),
             "coverage": float(inside.mean()), "n_inside": int(inside.sum()),
             "width_by_horizon": {str(h): float(width[h - 1]) for h in (1, 5, 10, 15, 20, 25, 30)},
             "width_mean_wk": [float(width[i:i + 7].mean()) for i in (0, 7, 14, 21)],
             "mean_width": float(width.mean()), "converged": bool(f.mle_retvals.get("converged", False)),
             "live_parity": parity,
             "actual": [float(v) for v in act], "pred": [float(v) for v in pt],
             "lo": [float(v) for v in ci[:, 0]], "hi": [float(v) for v in ci[:, 1]]}
    print("holdout", t, flush=True)
out["phase4"] = p4
json.dump(out, open(HERE / "results.json", "w"), indent=1, default=float)
print("WROTE results.json")
