"""Box-Jenkins analysis of Northside's LIVE 420-day demonstration series,
SYNTHETIC-ONLY (see the export note below), running the SAME per-type order-
selection grid search the live system uses (main.py:_fit_candidate_order /
_rank_candidates_and_select, imported directly, not reimplemented) and then
keeping the SELECTION FIT and the PRODUCTION FIT strictly separate throughout:

  SELECTION FIT  = fit on the TRAINING window only (series minus the last 30
                   days) — what the 72-candidate grid search (d in {0,1} searched, not assumed) and its AIC/
                   Ljung-Box criterion actually judged. This is what
                   server/select_orders.py / _run_order_selection_for_facility
                   runs against a live facility.
  PRODUCTION FIT = fit on the FULL series — what a live forecast request
                   (_fit_and_cache_sarimax) actually runs, day to day, using
                   whatever order was selected.

Never present one as the other (see PHASE_3_DIAGNOSTICS.md's "two fits"
section). A STABILITY check additionally re-runs selection with the training
window cut a further 30 days shorter, to see whether the winning order for
each type is sensitive to exactly where the window ends.

EXPORT NOTE: northside_420d_history.csv now ends 2026-09-26 and is entirely
the uploaded synthetic file (upload_history_id=224) — the one real organic day
that used to be spliced onto the end (2026-09-27, from live blood_units) has
been dropped. That splice was a genuine problem: for B- it was a 7.6-standard-
deviation outlier (JB stat 4138.7, kurtosis 18.15) that vanished entirely once
removed (see git history / the chat record for the investigation). This report
does not read facility_sarimax_order (the live table) at all — every order
here is selected fresh, on this file, so the report is fully self-contained
and reproducible independent of whatever the live database currently holds.

GENERATED DEMONSTRATION DATA throughout, not real blood bank records. Every
number in docs/methodology/PHASE_*.md comes from this script's output
(results.json). Run from the repo root:
    server\\.venv\\Scripts\\python.exe docs\\methodology\\run_methodology.py
"""
import csv
import itertools
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, adfuller, pacf
from statsmodels.tsa.statespace.sarimax import SARIMAX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "server"))
import main as app  # live constants + dengue_season_index + the grid-search building blocks

warnings.simplefilter("ignore")
MAXITER = app.FACILITY_SARIMAX_MAXITER
HOLDOUT = app.ORDER_SELECTION_HOLDOUT_DAYS  # 30 — the SAME split the live selection mechanism uses
STABILITY_EXTRA_CUT = 30  # the stability check's training window ends this many days earlier still
GRID = list(itertools.product(app.SARIMAX_ORDER_GRID_PQ, app.SARIMAX_ORDER_GRID_PQ, app.SARIMAX_ORDER_GRID_D,
                              app.SARIMAX_ORDER_GRID_SEASONAL_PQ, app.SARIMAX_ORDER_GRID_SEASONAL_PQ))
assert len(GRID) == 72  # d is searched now, not fixed at 1 — see main.py's ORDER SELECTION module comment

df = pd.DataFrame(list(csv.DictReader(open(HERE / "northside_420d_history.csv"))))
df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
df["units"] = df["units"].astype(float)
wide = df.pivot(index="snapshot_date", columns="blood_type", values="units").sort_index()
N_DAYS = len(wide)
assert not wide.isna().any().any()
assert (wide.index == pd.date_range(wide.index[0], periods=N_DAYS, freq="D")).all(), "gaps in dates"
TYPES = list(wide.columns)
exog_all = pd.DataFrame({"dengue_season": [app.dengue_season_index(d.date()) for d in wide.index]}, index=wide.index)

out = {"meta": {"days": N_DAYS, "first": str(wide.index[0].date()), "last": str(wide.index[-1].date()),
                "types": TYPES, "csv": "northside_420d_history.csv", "holdout_days": HOLDOUT,
                "data_notice": "GENERATED DEMONSTRATION DATA, not real blood bank records; not collected data. "
                               "The organic 2026-09-27 splice day has been dropped — see the module docstring."}}


def fit_order(y, x, order, sorder):
    m = SARIMAX(y, exog=x, order=order, seasonal_order=sorder, trend=None,
                enforce_stationarity=False, enforce_invertibility=False)
    return m.fit(disp=False, maxiter=MAXITER)


def params_of(f):
    return {k: {"coef": float(f.params[k]), "se": float(f.bse[k]), "z": float(f.tvalues[k]), "p": float(f.pvalues[k])}
            for k in f.params.index}


def diagnostics_of(f, K):
    """Full in-sample diagnostic suite for one fit: Ljung-Box (uncorrected +
    K-corrected) at 7/14/21/28, Jarque-Bera, heteroskedasticity, residual ACF."""
    burn = max(int(getattr(f, "loglikelihood_burn", 0)), 1)
    resid = np.asarray(f.standardized_forecasts_error[0])[burn:]
    resid = resid[np.isfinite(resid)]
    lb = {}
    for lag in (7, 14, 21, 28):
        res = acorr_ljungbox(resid, lags=[lag], model_df=0)
        stat, up = float(res["lb_stat"].iloc[0]), float(res["lb_pvalue"].iloc[0])
        dfree = lag - K
        corrected = float(chi2.sf(stat, dfree)) if dfree > 0 else None
        lb[lag] = {"p_uncorrected": up, "p_corrected": corrected}
    jb = f.test_normality("jarquebera")[0]
    bv = f.test_heteroskedasticity("breakvar")[0]
    bd = 1.96 / np.sqrt(len(resid))
    ra = acf(resid, nlags=28, fft=False)
    return {"n_resid": len(resid), "burn": burn, "K": K, "ljungbox": lb,
            "jb": {"stat": float(jb[0]), "p": float(jb[1]), "skew": float(jb[2]), "kurt": float(jb[3])},
            "breakvar": {"stat": float(bv[0]), "p": float(bv[1])},
            "resid_acf_bound": bd, "resid_acf_outside": [i for i in range(1, 29) if abs(ra[i]) > bd]}


def run_grid(y, x):
    """The SAME 72-candidate grid (d in {0,1} searched, not assumed), via the
    SAME live functions (app._fit_candidate_order / app._rank_candidates_and_select)
    — not a reimplementation. Returns (sorted_by_aic, winner)."""
    candidates = [c for (p, q, d, sp, sq) in GRID if (c := app._fit_candidate_order(y, x, p, d, q, sp, sq)) is not None]
    winner = app._rank_candidates_and_select(candidates)
    candidates.sort(key=lambda c: c["aic"])
    return candidates, winner


# ---------------- Phase 1: identification (data only, full series) ----------------
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

# step-check (data-shape descriptive; unrelated to order)
jun1 = pd.Timestamp("2026-06-01")
out["step_check"] = {
    t: {"mean_7d_before_jun1": float(wide[t][jun1 - pd.Timedelta(days=7):jun1 - pd.Timedelta(days=1)].mean()),
        "mean_7d_after_jun1": float(wide[t][jun1:jun1 + pd.Timedelta(days=6)].mean())}
    for t in TYPES}

# ---------------- SELECTION FIT + PRODUCTION FIT + Phase 4 holdout ----------------
selection, production, phase4 = {}, {}, {}
t0_all = time.time()
for t in TYPES:
    y_full, x_full = wide[t], exog_all
    y_tr, x_tr = y_full.iloc[:-HOLDOUT], x_full.iloc[:-HOLDOUT]
    y_ho, x_ho = y_full.iloc[-HOLDOUT:], x_full.iloc[-HOLDOUT:]
    t0 = time.time()

    # --- SELECTION FIT: 72-candidate grid (d searched) on the TRAINING window only ---
    candidates, winner = run_grid(y_tr, x_tr)
    order = (winner["p"], winner["d"], winner["q"])
    sorder = (winner["seasonal_p"], winner["seasonal_d"], winner["seasonal_q"], winner["seasonal_s"])
    K = winner["p"] + winner["q"] + winner["seasonal_p"] + winner["seasonal_q"]
    f_sel = fit_order(y_tr, x_tr, order, sorder)  # refit once more to get the full params table
    top5 = candidates[:5]
    # "Gap to runner-up" is relative to the WINNER, which is not always the lowest-AIC
    # candidate overall (the selection rule picks lowest AIC AMONG THOSE THAT PASS Ljung-Box;
    # a lower-AIC candidate that fails the criterion is not the winner — see PHASE_2's A+ row,
    # where the two best-AIC candidates both failed and the actual winner ranks 3rd by raw AIC).
    # So the "pool" the winner was drawn from — the passers, or all 36 if none passed — is what
    # matters, and the runner-up is the next-best AIC within THAT pool.
    passers = [c for c in candidates if all(v is not None and v > 0.05 for v in c["lb_p"].values())]
    pool = sorted(passers if passers else candidates, key=lambda c: c["aic"])
    aic_gap = (pool[1]["aic"] - pool[0]["aic"]) if len(pool) > 1 else None
    assert abs(pool[0]["aic"] - winner["aic"]) < 1e-6, "pool[0] must be the winner"
    selection[t] = {
        "window": {"start": str(y_tr.index[0].date()), "end": str(y_tr.index[-1].date()), "n_days": len(y_tr)},
        "order": order, "seasonal_order": sorder, "aic": float(f_sel.aic), "bic": float(f_sel.bic),
        "params": params_of(f_sel), "criterion_satisfied": winner["criterion_satisfied"],
        "n_candidates_converged": winner["n_candidates_converged"],
        "grid_top5": [{"order": (c["p"], c["d"], c["q"]), "seasonal_order": (c["seasonal_p"], c["seasonal_d"], c["seasonal_q"], c["seasonal_s"]),
                       "aic": c["aic"], "gap_to_winner": c["aic"] - candidates[0]["aic"]} for c in top5],
        "aic_gap_to_runnerup": aic_gap,
        "diagnostics": diagnostics_of(f_sel, K),
    }

    # --- PRODUCTION FIT: the SAME selected order, on the FULL series ---
    f_prod = fit_order(y_full, x_full, order, sorder)
    production[t] = {"order": order, "seasonal_order": sorder, "aic": float(f_prod.aic), "bic": float(f_prod.bic),
                     "params": params_of(f_prod), "diagnostics": diagnostics_of(f_prod, K)}

    # --- Phase 4: forecast the hold-out from the SELECTION fit (reuse f_sel, no refit) ---
    fc = f_sel.get_forecast(steps=HOLDOUT, exog=x_ho)
    pt = fc.predicted_mean.values
    ci = np.asarray(fc.conf_int(alpha=0.05))
    act = y_ho.values
    naive = np.full(HOLDOUT, y_tr.iloc[-1])
    snaive = np.array([y_tr.iloc[-7 + (i % 7)] for i in range(HOLDOUT)])

    def m(pred):
        e = act - pred
        return {"mape": float(np.mean(np.abs(e) / act) * 100), "rmse": float(np.sqrt(np.mean(e ** 2))), "mae": float(np.mean(np.abs(e)))}
    inside = (act >= ci[:, 0]) & (act <= ci[:, 1])
    width = ci[:, 1] - ci[:, 0]

    # One-step-ahead hold-out Ljung-Box: fit.append(refit=False), NOT the static multi-step
    # errors above — those are autocorrelated by construction from a fixed origin and invalid
    # for this test (see PHASE_3's method note). Reuses f_sel, no refit.
    lb_onestep = {lag: None for lag in app.SARIMAX_ORDER_GRID_LAGS}
    try:
        extended = f_sel.append(y_ho, exog=x_ho, refit=False)
        onestep = np.asarray(extended.standardized_forecasts_error[0])[-HOLDOUT:]
        onestep = onestep[np.isfinite(onestep)]
        for lag in app.SARIMAX_ORDER_GRID_LAGS:
            if len(onestep) > lag:
                lb_onestep[lag] = float(acorr_ljungbox(onestep, lags=[lag], model_df=0)["lb_pvalue"].iloc[0])
    except Exception:
        pass

    # live-path parity: the real production function, this type's SELECTED order, same training series
    daily = [(idx.date(), int(v)) for idx, v in y_tr.items()]
    label = app._order_label(*order, *sorder, selected=True)
    live = app._fit_sarimax_facility_forecast(daily, y_tr.index[-1].date(), order=order, seasonal_order=sorder, model_label=label)
    parity = None
    if live:
        parity = {str(c): {"live": live["checkpoints"][c]["units"], "mine": int(round(max(0, pt[c - 1])))}
                  for c in app.FORECAST_CHECKPOINTS if c > 0}
    phase4[t] = {"order": order, "seasonal_order": sorder,
                 "sarimax": m(pt), "naive_last": m(naive), "seasonal_naive_7": m(snaive),
                 "coverage": float(inside.mean()), "n_inside": int(inside.sum()),
                 "width_by_horizon": {str(h): float(width[h - 1]) for h in (1, 5, 10, 15, 20, 25, 30)},
                 "lb_onestep_holdout": lb_onestep,
                 "live_parity": parity, "live_parity_match": bool(parity and all(v["live"] == v["mine"] for v in parity.values())),
                 "point_forecast": pt.tolist(), "actual": act.tolist(), "series_mean": float(y_full.mean())}
    print(f"{t}: selection+production+phase4 done in {time.time() - t0:.1f}s", flush=True)
out["selection"], out["production"], out["phase4"] = selection, production, phase4
print(f"all 8 types: {time.time() - t0_all:.1f}s total", flush=True)

# ---------------- Stability check: training window cut 30 days shorter still ----------------
# Instability COST: order_b (selected 30 days earlier) refit on window_a's own
# training data (same data order_a was selected/fit on) and forecast the SAME
# holdout, isolating what just the order choice costs, holding the data fixed.
# Also checks whether order_b still satisfies the Ljung-Box criterion on window_a's
# data — "a selection made 30 days earlier" tested against the later window.
stability = {}
t0 = time.time()
for t in TYPES:
    y_short, x_short = wide[t].iloc[:-(HOLDOUT + STABILITY_EXTRA_CUT)], exog_all.iloc[:-(HOLDOUT + STABILITY_EXTRA_CUT)]
    _, winner_short = run_grid(y_short, x_short)
    order_391 = selection[t]["order"] + selection[t]["seasonal_order"]
    order_361 = (winner_short["p"], winner_short["d"], winner_short["q"],
                winner_short["seasonal_p"], winner_short["seasonal_d"], winner_short["seasonal_q"], winner_short["seasonal_s"])

    y_full, x_full = wide[t], exog_all
    y_tr, x_tr = y_full.iloc[:-HOLDOUT], x_full.iloc[:-HOLDOUT]
    y_ho, x_ho = y_full.iloc[-HOLDOUT:], x_full.iloc[-HOLDOUT:]
    p_b, d_b, q_b, sp_b, sd_b, sq_b, ss_b = order_361
    f_b = fit_order(y_tr, x_tr, (p_b, d_b, q_b), (sp_b, sd_b, sq_b, ss_b))
    fc_b = f_b.get_forecast(steps=HOLDOUT, exog=x_ho)
    pt_b = fc_b.predicted_mean.values
    act = np.array(phase4[t]["actual"])
    e_b = act - pt_b
    metrics_b = {"mape": float(np.mean(np.abs(e_b) / act) * 100), "rmse": float(np.sqrt(np.mean(e_b ** 2)))}

    pt_a = np.array(phase4[t]["point_forecast"])
    abs_diff = np.abs(pt_a - pt_b)
    series_mean = phase4[t]["series_mean"]
    mean_abs_diff, max_abs_diff = float(abs_diff.mean()), float(abs_diff.max())

    cand_b = app._fit_candidate_order(y_tr, x_tr, p_b, d_b, q_b, sp_b, sq_b)
    order_b_passes_on_window_a = bool(cand_b and all(v is not None and v > 0.05 for v in cand_b["lb_p"].values()))

    stability[t] = {
        "window_a": selection[t]["window"],
        "window_b": {"start": str(y_short.index[0].date()), "end": str(y_short.index[-1].date()), "n_days": len(y_short)},
        "order_a": order_391, "order_b": order_361, "stable": order_391 == order_361,
        "instability_cost": {
            "mape_a": phase4[t]["sarimax"]["mape"], "rmse_a": phase4[t]["sarimax"]["rmse"],
            "mape_b": metrics_b["mape"], "rmse_b": metrics_b["rmse"],
            "mean_abs_diff": mean_abs_diff, "max_abs_diff": max_abs_diff,
            "series_mean": series_mean,
            "mean_abs_diff_pct": mean_abs_diff / series_mean * 100,
            "max_abs_diff_pct": max_abs_diff / series_mean * 100,
            "order_a_passes": selection[t]["criterion_satisfied"],
            "order_b_passes_on_window_a": order_b_passes_on_window_a,
        },
    }
out["stability"] = stability
print(f"stability check: {time.time() - t0:.1f}s ->", {t: stability[t]["stable"] for t in TYPES}, flush=True)

json.dump(out, open(HERE / "results.json", "w"), indent=1, default=float)
print("WROTE results.json")
