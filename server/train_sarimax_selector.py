"""Trains and evaluates the AI-assisted SARIMAX model-selection classifier —
the emerging-technology piece that replaces the hardcoded
SARIMAX(0,1,4)x(1,0,1,7) default with a per-facility, per-blood-type choice
from a curated candidate library (sarimax_selector_common.CANDIDATE_LIBRARY).

SARIMAX still produces every forecast number; the classifier only ever picks
WHICH config SARIMAX fits with. It never predicts raw parameters or touches
a forecast value.

SECOND ATTEMPT. The first attempt labeled each synthetic series with the
best-AIC candidate (estimate_synthetic_forecast_models.py's own rule) and
hit its own STOP condition: even the best-AIC oracle, given every candidate
and full information, didn't beat the fixed default on held-out forecast
accuracy. AIC measures in-sample fit quality, not out-of-sample forecast
accuracy — training a classifier to predict a label that itself doesn't
track the metric that actually matters can't produce something useful,
however good the classifier gets at predicting that label.

What's different this time:
  1. Labels come from rolling-origin cross-validated forecast error (mean
     MAPE across held-out CV origins), not AIC — this directly targets the
     metric a forecast is judged on
     (sarimax_selector_common.best_candidate_by_forecast_error).
  2. An explicit STAGE 1 GATE runs BEFORE any classifier training: does
     per-series config selection (the "oracle" — the same forecast-error
     rule, given full information) actually beat the fixed default on
     genuinely held-out data, and does the winning config actually vary
     across series? If not, this STOPs immediately and reports why,
     without ever training a tree against a target that can't win.

Methodology (kept strictly non-leaky — see the per-series data flow below):
  For every series, the FINAL EVAL_HORIZON days are reserved and never used
  for labeling, feature extraction, or config selection — only for the one
  true "did this actually work" comparison at the end. Everything upstream
  of that (rolling-origin CV origins, feature extraction) only ever sees the
  "history" portion (series minus the final EVAL_HORIZON days) — exactly
  what a real facility would have on file at forecast time.

    |----------------- history (used for CV/labeling/features) -----|-- final EVAL_HORIZON (only for scoring) --|
    |-- CV origin 1 --|-- CV origin 2 (if it fits) --|                |
                        (up to LABEL_CV_ORIGINS rolling-origin windows, each LABEL_CV_HORIZON days)

Run manually (like every other script in this pipeline):
    .venv\\Scripts\\python.exe train_sarimax_selector.py

Flags:
  --force-stage2  run Stage 2 even if the Stage 1 gate fails. The gate still
                  runs and its verdict stands; the artifacts are stamped
                  deploy_recommended=False. Answers "can we train one", not
                  "should we deploy it".
  --quick         much smaller N_* sample sizes — a demo run, not evidence.
  --out-dir DIR   where the joblib + rules JSON go (default: server/models/).
"""

import argparse
import json
import random
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import sklearn
from joblib import Parallel, delayed
from sklearn.tree import DecisionTreeClassifier, export_text
from statsmodels.tsa.statespace.sarimax import SARIMAX

from sarimax_selector_common import (
    CANDIDATE_LIBRARY,
    FEATURE_NAMES,
    SARIMAX_DEFAULT_INDEX,
    SARIMAX_DEFAULT_ORDER,
    SARIMAX_DEFAULT_SEASONAL_ORDER,
    best_candidate_by_forecast_error,
    extract_series_features,
    features_to_vector,
    fit_candidate,
    recommend_candidate_index_from_rules,
)

DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}  # must match main.py's DENGUE_SEASON_MONTHS

RANDOM_SEED = 11
GATE_SEED_OFFSET, TRAIN_SEED_OFFSET, EVAL_SEED_OFFSET = 500, 1000, 9000
N_GATE_SERIES = 70  # Stage 1 gate sample
N_TRAIN_SERIES = 220
N_EVAL_SERIES = 60
# --quick: a demo run sized to the wall-clock budget. Fewer series alone can't hit
# it (one long series per batch is the floor), so CV origins drop too — noisier labels.
QUICK = False
QUICK_N_GATE, QUICK_N_TRAIN, QUICK_N_EVAL = 16, 32, 16
QUICK_LABEL_CV_ORIGINS = 2

EVAL_HORIZON = 14  # the true, final held-out horizon every series reserves — UNCHANGED from attempt 2
# THIRD ATTEMPT — the one lever under test: more and longer rolling-origin CV
# windows per series for labeling, to de-noise the per-series "forecast-best"
# label. Attempt 2 used LABEL_CV_HORIZON=14, LABEL_CV_ORIGINS=2 (up to 2
# windows of 14 days each = up to 28 days of CV evidence per label). This
# attempt: LABEL_CV_HORIZON=21, LABEL_CV_ORIGINS=4 (up to 4 windows of 21
# days each = up to 84 days of CV evidence per label — 3x the windows, 1.5x
# longer each). Nothing else changed: same CANDIDATE_LIBRARY, same
# sample_series_params ranges, same EVAL_HORIZON for the final true test.
# Real, honest consequence of this specific lever: rolling_origin_windows
# requires >= LABEL_CV_HORIZON days of training history before each origin,
# so the shortest series in the population (length=45, history=31 after
# reserving EVAL_HORIZON) can no longer support even one CV window at
# horizon=21 and gets dropped from the labelable set entirely — not a bug,
# not population tuning, just what "longer windows" structurally costs for
# facilities with the least history. Reported plainly below, not hidden.
LABEL_CV_HORIZON = 21
LABEL_CV_ORIGINS = 4
LABEL_FIT_MAXITER = 60
DEPLOY_FIT_MAXITER = 100  # maxiter when fitting the chosen config forward for the final scored forecast

TREE_MAX_DEPTH = 4
TREE_MIN_SAMPLES_LEAF = 12

MODEL_PATH = Path(__file__).parent / "models" / "sarimax_selector.joblib"

DATE_POOL_START = date(2022, 1, 1)
DATE_POOL_END = date(2025, 6, 1)


def _dengue_flag(dates: list[date]) -> np.ndarray:
    return np.array([1.0 if d.month in DENGUE_SEASON_MONTHS else 0.0 for d in dates])


def generate_diverse_series(rng: random.Random, length: int, baseline: float, dengue_trough_mult: float,
                             trend_frac: float, noise_frac: float, weekly_amp: float,
                             start_date: date) -> tuple[list[date], np.ndarray]:
    """Same mean-reverting level + periodic-restock + noise mechanics as
    generate_synthetic_forecast_data.py's generate_series, parametrized on 5
    independent knobs. weekly_amp controls the PROBABILITY each restock
    lands on an exact 7-day cadence vs. a random 5-9 day one (not a small
    ripple layered on top of random timing — verified via a standalone
    diagnostic during the first attempt that a ripple gets swamped by the
    restock jumps themselves and produces no detectable period-7 signal;
    locking the cadence itself does)."""
    dates = [start_date + timedelta(days=i) for i in range(length)]
    dengue = _dengue_flag(dates)

    values = np.empty(length)
    level = baseline
    days_until_restock = 7 if rng.random() < weekly_amp else rng.randint(5, 9)
    for i in range(length):
        seasonal_mult = 1.0 - (1.0 - dengue_trough_mult) * dengue[i]
        trend_mult = 1.0 + trend_frac * (i / max(length - 1, 1))
        target = baseline * seasonal_mult * trend_mult

        days_until_restock -= 1
        if days_until_restock <= 0:
            level = target * rng.uniform(1.15, 1.35)
            days_until_restock = 7 if rng.random() < weekly_amp else rng.randint(5, 9)

        draw = target * 0.06 + rng.gauss(0, max(1.0, target * noise_frac))
        level = max(0.0, level - max(0.0, draw))
        values[i] = round(level)

    return dates, values


def sample_series_params(rng: random.Random) -> dict:
    """A genuinely heterogeneous facility population — small vs large
    (baseline), strong vs weak/absent weekly seasonality (weekly_amp),
    dengue-heavy vs flat (dengue_trough_mult), trending vs stable
    (trend_frac), high vs low noise (noise_frac) — all drawn from wide,
    independent, realistic ranges. Not hand-picked scenarios: plain random
    sampling across the ranges is what keeps this an honest test rather
    than a rigged one (see the module-level honesty note in the task this
    script was written for — the ranges themselves are the only lever
    pulled, never which specific series get generated).
    """
    length = rng.choice([45, 60, 90, 120, 150, 200, 300, 400])
    days_span = (DATE_POOL_END - DATE_POOL_START).days - length - EVAL_HORIZON - 5
    start_date = DATE_POOL_START + timedelta(days=rng.randint(0, max(days_span, 1)))
    return {
        "length": length,
        "baseline": rng.uniform(20, 200),
        "dengue_trough_mult": rng.uniform(0.30, 1.0),  # 1.0 = no seasonal effect at all
        "trend_frac": rng.uniform(-0.5, 0.5),
        "noise_frac": rng.uniform(0.01, 0.12),
        "weekly_amp": rng.uniform(0.0, 1.0),  # 0 = no weekly pattern at all
        "start_date": start_date,
    }


def _split_series(dates: list[date], values: np.ndarray) -> Optional[tuple]:
    """Splits one generated series into (history_dates, history_values,
    final_dates, final_values) — the final EVAL_HORIZON days are reserved
    and never touched by labeling/CV/feature extraction. None if too short
    to reserve a final horizon at all."""
    n = len(values)
    if n <= EVAL_HORIZON + LABEL_CV_HORIZON:  # need at least one CV origin's worth of history too
        return None
    split = n - EVAL_HORIZON
    return dates[:split], values[:split], dates[split:], values[split:]


def _build_endog_exog(dates: list[date], values) -> tuple[pd.Series, pd.DataFrame]:
    idx = pd.DatetimeIndex(dates)
    endog = pd.Series(values, index=idx, dtype=float)
    exog = pd.DataFrame({"dengue_season": _dengue_flag(dates)}, index=idx)
    return endog, exog


def _deploy_and_score(history_endog, history_exog, final_endog, final_exog, order, seasonal_order, maxiter):
    """Fits `order`/`seasonal_order` on the FULL history and scores its
    forecast against the FINAL held-out horizon's real values — the one
    true, non-leaky "did this choice actually work" measurement. Returns
    (errors_dict_or_None, elapsed_seconds)."""
    t0 = time.time()
    fit_result = fit_candidate(history_endog, history_exog, order, seasonal_order, maxiter=maxiter)
    if fit_result is None:
        return None, time.time() - t0
    try:
        fc = fit_result["fit"].get_forecast(steps=len(final_endog), exog=final_exog).predicted_mean.values
    except Exception:
        return None, time.time() - t0
    elapsed = time.time() - t0
    if not np.all(np.isfinite(fc)):
        return None, elapsed
    actual = final_endog.values
    pct_err = np.abs((actual - fc) / np.where(actual == 0, np.nan, actual))
    return {
        "rmse": float(np.sqrt(np.mean((actual - fc) ** 2))),
        "mape": float(np.nanmean(pct_err) * 100) if np.any(np.isfinite(pct_err)) else None,
    }, elapsed


def _process_one_series(seed: int, params: dict, need_features: bool, cv_origins: int) -> Optional[dict]:
    """Picklable top-level worker for joblib.Parallel. For one series:
      - selects the forecast-error-best config using ONLY the history
        portion (rolling-origin CV) — this is both the training label AND
        the "oracle"/full-search reference at gate/eval time, since it's
        the same rule applied the same way;
      - deploys that config forward on the full history and scores it
        against the true final held-out horizon (non-leaky);
      - does the same for the fixed default, for comparison;
      - extracts features from history only, if requested (skipped for the
        Stage 1 gate, which doesn't need them).
    Returns None if the series is too short, or the CV step found no
    survivable candidate at all.
    """
    rng = random.Random(seed)
    dates, values = generate_diverse_series(rng, **params)
    split = _split_series(dates, values)
    if split is None:
        return None
    hist_dates, hist_values, final_dates, final_values = split

    hist_endog, hist_exog = _build_endog_exog(hist_dates, hist_values)
    final_endog, final_exog = _build_endog_exog(final_dates, final_values)

    cv_result = best_candidate_by_forecast_error(
        hist_endog, hist_exog, library=CANDIDATE_LIBRARY,
        horizon=LABEL_CV_HORIZON, n_origins=cv_origins, maxiter=LABEL_FIT_MAXITER,
    )
    if cv_result is None:
        return None
    best_idx, cv_scores = cv_result
    best_order, best_seasonal = CANDIDATE_LIBRARY[best_idx]

    oracle_errors, oracle_time = _deploy_and_score(
        hist_endog, hist_exog, final_endog, final_exog, best_order, best_seasonal, DEPLOY_FIT_MAXITER
    )
    default_errors, default_time = _deploy_and_score(
        hist_endog, hist_exog, final_endog, final_exog,
        SARIMAX_DEFAULT_ORDER, SARIMAX_DEFAULT_SEASONAL_ORDER, DEPLOY_FIT_MAXITER,
    )

    result = {
        "label_idx": best_idx,
        "cv_scores": cv_scores,
        "oracle_errors": oracle_errors,
        "oracle_time": oracle_time,
        "default_errors": default_errors,
        "default_time": default_time,
        "hist_values": hist_values,
        "final_endog": final_endog,
        "final_exog": final_exog,
        "hist_endog": hist_endog,
        "hist_exog": hist_exog,
        "seed": seed,
        "length": len(values),
    }
    if need_features:
        result["features"] = extract_series_features(hist_values)
    return result


def _run_batch(n_series: int, seed_offset: int, need_features: bool) -> list[dict]:
    label_cv_origins = QUICK_LABEL_CV_ORIGINS if QUICK else LABEL_CV_ORIGINS
    rng = random.Random(RANDOM_SEED + seed_offset)
    param_sets = [sample_series_params(rng) for _ in range(n_series)]
    seeds = [rng.randint(0, 2**31 - 1) for _ in range(n_series)]
    t0 = time.time()
    results = Parallel(n_jobs=-1, verbose=5)(
        delayed(_process_one_series)(seed, params, need_features, label_cv_origins) for seed, params in zip(seeds, param_sets)
    )
    print(f"processed {n_series} series in {time.time() - t0:.1f}s")
    return [r for r in results if r is not None]


def _summarize_errors(rows: list[dict], key: str) -> dict:
    scored = [r[key] for r in rows if r[key] is not None and r[key]["mape"] is not None]
    return {
        "n": len(scored),
        "n_total": len(rows),
        "rmse": float(np.mean([e["rmse"] for e in scored])) if scored else float("nan"),
        "mape": float(np.mean([e["mape"] for e in scored])) if scored else float("nan"),
    }


def _scored(errors: Optional[dict]) -> bool:
    return errors is not None and errors.get("mape") is not None


def _paired_summary(eval_rows: list[dict], ai_results: list[dict], diagnose=None) -> dict:
    """AI vs default vs oracle on ONLY the series all three scored. Comparing each
    method's mean over whichever series IT happened to score is not a comparison when
    the sets differ (n=52 vs n=51 once): the difference could be one series. A dropped
    series is named (seed, length) with the reason per method: "no_forecast" = the fit
    didn't converge / failed the finite check, or the forecast was non-finite;
    "mape_undefined" = every held-out actual was zero. `diagnose(row, ai_result, method)`, when
    given, replaces "no_forecast" with the specific cause (see _diagnose_dropped)."""
    kept, dropped = [], []
    for row, ai in zip(eval_rows, ai_results):
        methods = {"ai": ai["errors"], "default": row["default_errors"], "oracle": row["oracle_errors"]}
        bad = {
            m: ((diagnose(row, ai, m) if diagnose else "no_forecast") if e is None else "mape_undefined")
            for m, e in methods.items() if not _scored(e)
        }
        if bad:
            dropped.append({"seed": row["seed"], "length": row["length"], "unscored": bad})
        else:
            kept.append(methods)
    out = {"basis": "paired", "n_paired": len(kept), "n_eval_series": len(eval_rows), "dropped_series": dropped}
    for m in ("ai", "default", "oracle"):
        out[m] = {
            "n": len(kept),
            "rmse": float(np.mean([k[m]["rmse"] for k in kept])) if kept else float("nan"),
            "mape": float(np.mean([k[m]["mape"] for k in kept])) if kept else float("nan"),
        }
    return out


def _diagnose_dropped(row: dict, ai: dict, method: str) -> str:
    """Why `method` has no forecast on this series: refits the same config on the same history
    and reports which gate in fit_candidate/_deploy_and_score rejected it."""
    order, seasonal_order = {
        "ai": CANDIDATE_LIBRARY[ai["idx"]], "default": (SARIMAX_DEFAULT_ORDER, SARIMAX_DEFAULT_SEASONAL_ORDER),
        "oracle": CANDIDATE_LIBRARY[row["label_idx"]],
    }[method]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(row["hist_endog"], exog=row["hist_exog"], order=order, seasonal_order=seasonal_order, trend=None,
                          enforce_stationarity=False, enforce_invertibility=False).fit(disp=False, maxiter=DEPLOY_FIT_MAXITER)
    except Exception as exc:
        return f"fit_raised_{type(exc).__name__}"
    history_days = len(row["hist_endog"])
    if not fit.mle_retvals.get("converged", False):
        return f"optimizer_did_not_converge_in_{DEPLOY_FIT_MAXITER}_iterations ({order}x{seasonal_order} on {history_days} days of history)"
    if not (np.isfinite(fit.aic) and np.isfinite(fit.bic)):
        return "non_finite_aic_or_bic"
    return "forecast_failed_or_non_finite"


def _split_features(node: dict) -> list[str]:
    if "leaf" in node:
        return []
    return sorted({node["feature"], *_split_features(node["left"]), *_split_features(node["right"])})


def run_stage1_gate(force: bool = False) -> tuple[bool, list[str]]:
    print(f"=== STAGE 1 GATE: oracle (forecast-error-selected config) vs fixed default, {N_GATE_SERIES} heterogeneous series ===")
    rows = _run_batch(N_GATE_SERIES, seed_offset=GATE_SEED_OFFSET, need_features=False)
    print(f"{len(rows)}/{N_GATE_SERIES} series survived (had a CV-survivable candidate and a scorable final forecast)")

    oracle_summary = _summarize_errors(rows, "oracle_errors")
    default_summary = _summarize_errors(rows, "default_errors")

    label_counts = pd.Series([r["label_idx"] for r in rows]).value_counts().sort_index()
    print("\nOracle (forecast-error-best) config distribution across gate series:")
    for idx, count in label_counts.items():
        order, seasonal = CANDIDATE_LIBRARY[idx]
        print(f"  [{idx}] {order}x{seasonal}: {count} series")

    print(f"\n{'method':<12}{'n':>5}{'RMSE':>10}{'MAPE%':>10}")
    print(f"{'Oracle':<12}{oracle_summary['n']:>5}{oracle_summary['rmse']:>10.3f}{oracle_summary['mape']:>10.2f}")
    print(f"{'Default':<12}{default_summary['n']:>5}{default_summary['rmse']:>10.3f}{default_summary['mape']:>10.2f}")

    beats_default_rmse = oracle_summary["rmse"] < default_summary["rmse"]
    beats_default_mape = oracle_summary["mape"] < default_summary["mape"]
    rmse_margin_pct = (1 - oracle_summary["rmse"] / default_summary["rmse"]) * 100
    mape_margin_pct = (1 - oracle_summary["mape"] / default_summary["mape"]) * 100
    varies = len(label_counts) >= 3

    print("\n--- Stage 1 verdict ---")
    print(f"Oracle beats default on RMSE: {beats_default_rmse}  (margin {rmse_margin_pct:+.1f}%)")
    print(f"Oracle beats default on MAPE: {beats_default_mape}  (margin {mape_margin_pct:+.1f}%)")
    print(f"Winning config varies (>=3 distinct): {varies}  ({len(label_counts)} distinct)")

    # "Meaningful margin": beats default on both metrics by at least 5%,
    # not just nominally ahead — a 0.3% edge is noise, not a premise worth
    # building a classifier on.
    meaningful_margin = beats_default_rmse and beats_default_mape and rmse_margin_pct >= 5.0 and mape_margin_pct >= 5.0
    passed = meaningful_margin and varies

    reasons: list[str] = []
    if not passed:
        print("\nSTOP at Stage 1: the premise doesn't hold on this heterogeneous, non-rigged population.")
        if not (beats_default_rmse and beats_default_mape):
            reasons.append("The oracle (full information, forecast-error selection) doesn't clearly beat the fixed default.")
        elif not meaningful_margin:
            reasons.append(f"The oracle beats the default, but only marginally (RMSE {rmse_margin_pct:+.1f}%, MAPE {mape_margin_pct:+.1f}%) — not a meaningful margin.")
        if not varies:
            reasons.append(f"The winning config barely varies ({len(label_counts)} distinct) — most series want the same config regardless of their real differences.")
        for reason in reasons:
            print(reason)
        if force:
            print("\n!!! GATE FAILED — the verdict stands. Stage 2 runs anyway because --force-stage2 was passed. !!!")
        else:
            print("Per-facility config selection does not appear to genuinely help on this data. NOT training a classifier against this target.")
    else:
        print(f"\nGATE PASSED: oracle beats default by a meaningful margin (RMSE {rmse_margin_pct:+.1f}%, MAPE {mape_margin_pct:+.1f}%) and the winning config varies ({len(label_counts)} distinct). Proceeding to Stage 2.")

    return passed, reasons


def tree_to_rules(clf, feature_names: list[str]) -> dict:
    """Nested-dict export of a fitted DecisionTreeClassifier: internal nodes
    {"feature", "threshold", "left" (<=), "right" (>)}, leaves {"leaf": candidate
    index}. Evaluated by sarimax_selector_common.recommend_candidate_index_from_rules
    with no scikit-learn import."""
    t = clf.tree_

    def node(i: int) -> dict:
        if t.children_left[i] == -1:
            return {"leaf": int(clf.classes_[int(np.argmax(t.value[i][0]))])}
        return {
            "feature": feature_names[t.feature[i]], "threshold": float(t.threshold[i]),
            "left": node(t.children_left[i]), "right": node(t.children_right[i]),
        }

    return node(0)


def run_stage2_train_and_evaluate(force: bool = False, gate_passed: bool = True,
                                  gate_reasons: Optional[list[str]] = None, out_dir: Path = MODEL_PATH.parent,
                                  run_started: Optional[float] = None):
    if force and not gate_passed:
        print("\n" + "!" * 78)
        print("STAGE 2 IS RUNNING DESPITE A FAILED STAGE 1 GATE (--force-stage2).")
        for reason in gate_reasons or []:
            print(f"  gate failed because: {reason}")
        print("Results below show whether a classifier CAN be trained, not that it SHOULD be deployed.")
        print("!" * 78)
    print(f"\n=== STAGE 2: generating + labeling {N_TRAIN_SERIES} training series (forecast-error rule) ===")
    train_rows = _run_batch(N_TRAIN_SERIES, seed_offset=TRAIN_SEED_OFFSET, need_features=True)
    print(f"{len(train_rows)}/{N_TRAIN_SERIES} series labeled successfully")

    label_counts = pd.Series([r["label_idx"] for r in train_rows]).value_counts().sort_index()
    print("\nLabel distribution in training data:")
    for idx, count in label_counts.items():
        order, seasonal = CANDIDATE_LIBRARY[idx]
        print(f"  [{idx}] {order}x{seasonal}: {count} series")

    X_train = [features_to_vector(r["features"]) for r in train_rows]
    y_train = [r["label_idx"] for r in train_rows]

    print(f"\n--- training DecisionTreeClassifier (max_depth={TREE_MAX_DEPTH}) ---")
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH, min_samples_leaf=TREE_MIN_SAMPLES_LEAF, random_state=RANDOM_SEED,
    )
    clf.fit(X_train, y_train)
    train_acc = clf.score(X_train, y_train)
    print(f"training-set label-match accuracy (not the real metric — see evaluation below): {train_acc:.3f}")

    print("\nFeature importances:")
    for name, importance in sorted(zip(FEATURE_NAMES, clf.feature_importances_), key=lambda p: -p[1]):
        print(f"  {name:<20} {importance:.4f}")

    print("\nTree structure:")
    tree_text = export_text(clf, feature_names=FEATURE_NAMES)
    print(tree_text)

    print(f"\n=== evaluating on {N_EVAL_SERIES} held-out series (never used in training) ===")
    eval_rows = _run_batch(N_EVAL_SERIES, seed_offset=EVAL_SEED_OFFSET, need_features=True)
    print(f"{len(eval_rows)}/{N_EVAL_SERIES} eval series survived")

    library = CANDIDATE_LIBRARY
    ai_results = []
    for row in eval_rows:
        t0 = time.time()
        ai_idx = int(clf.predict([features_to_vector(row["features"])])[0])
        ai_order, ai_seasonal = library[ai_idx]
        ai_errors, fit_time = _deploy_and_score(
            row["hist_endog"], row["hist_exog"], row["final_endog"], row["final_exog"],
            ai_order, ai_seasonal, DEPLOY_FIT_MAXITER,
        )
        ai_total_time = (time.time() - t0)
        ai_results.append({"idx": ai_idx, "errors": ai_errors, "time": ai_total_time})

    ai_summary = _summarize_errors([{"x": r["errors"]} for r in ai_results], "x")
    ai_avg_time = float(np.mean([r["time"] for r in ai_results]))
    oracle_summary = _summarize_errors(eval_rows, "oracle_errors")
    oracle_avg_time = float(np.mean([r["oracle_time"] for r in eval_rows]))
    default_summary = _summarize_errors(eval_rows, "default_errors")
    default_avg_time = float(np.mean([r["default_time"] for r in eval_rows]))

    print("\n=== UNPAIRED means (each method over the series IT scored; for reference, NOT a comparison) ===")
    print(f"{'method':<24}{'n':>5}{'RMSE':>10}{'MAPE%':>10}{'avg time/series (s)':>22}")
    print(f"{'AI-recommended':<24}{ai_summary['n']:>5}{ai_summary['rmse']:>10.3f}{ai_summary['mape']:>10.2f}{ai_avg_time:>22.3f}")
    print(f"{'Fixed default':<24}{default_summary['n']:>5}{default_summary['rmse']:>10.3f}{default_summary['mape']:>10.2f}{default_avg_time:>22.3f}")
    print(f"{'Oracle (full CV search)':<24}{oracle_summary['n']:>5}{oracle_summary['rmse']:>10.3f}{oracle_summary['mape']:>10.2f}{oracle_avg_time:>22.3f}")

    ai_variety = len(set(r["idx"] for r in ai_results))
    ai_distribution = pd.Series([r["idx"] for r in ai_results]).value_counts()
    print(f"\nAI recommended {ai_variety} of {len(CANDIDATE_LIBRARY)} candidate configs across {len(ai_results)} held-out series:")
    for idx, count in ai_distribution.items():
        print(f"  [{idx}] {CANDIDATE_LIBRARY[idx]}: recommended for {count} series")

    paired = _paired_summary(eval_rows, ai_results, diagnose=_diagnose_dropped)
    print(f"\n=== PAIRED (primary): only the {paired['n_paired']}/{paired['n_eval_series']} series all three methods scored ===")
    for series in paired["dropped_series"]:
        print(f"  dropped: seed={series['seed']} length={series['length']} unscored={series['unscored']}")
    print(f"{'method':<24}{'n':>5}{'RMSE':>10}{'MAPE%':>10}")
    for label, key in (("AI-recommended", "ai"), ("Fixed default", "default"), ("Oracle (full CV search)", "oracle")):
        print(f"{label:<24}{paired[key]['n']:>5}{paired[key]['rmse']:>10.3f}{paired[key]['mape']:>10.2f}")

    # The stop condition is judged on the paired set, not on the unpaired means above.
    beats_default_rmse = paired["ai"]["rmse"] < paired["default"]["rmse"]
    beats_default_mape = paired["ai"]["mape"] < paired["default"]["mape"]
    varies_meaningfully = ai_variety >= 3

    print("\n=== STAGE 2 STOP CONDITION CHECK (paired set) ===")
    print(f"AI beats fixed default on RMSE: {beats_default_rmse}  ({paired['ai']['rmse']:.3f} vs {paired['default']['rmse']:.3f})")
    print(f"AI beats fixed default on MAPE: {beats_default_mape}  ({paired['ai']['mape']:.2f}% vs {paired['default']['mape']:.2f}%)")
    print(f"AI recommendations vary meaningfully (>=3 distinct configs): {varies_meaningfully}  ({ai_variety} distinct)")

    passed = varies_meaningfully and (beats_default_rmse or beats_default_mape)
    if not passed and not force:
        print("\nSTOP at Stage 2: the trained classifier does not clear the bar. NOT saving a model artifact, NOT integrating.")
        return None

    deploy_recommended = passed and gate_passed
    if passed and gate_passed:
        print("\nSTAGE 2 PASSED: saving model artifact.")
    elif passed:
        print("\nSTAGE 2 CHECK PASSED, but the Stage 1 gate FAILED: saving artifacts for inspection only (--force-stage2).")
    else:
        print("\nSTAGE 2 CHECK FAILED: saving artifacts anyway (--force-stage2) for inspection only.")
    print(f"deploy_recommended = {deploy_recommended}  (gate_passed={gate_passed}, stage2_passed={passed})")

    rules = tree_to_rules(clf, FEATURE_NAMES)
    # Prove the dependency-free rules match the sklearn tree on every row either
    # set ever saw, before trusting them.
    check_rows = [r["features"] for r in train_rows + eval_rows]
    agree = sum(
        recommend_candidate_index_from_rules(f, {"tree": rules}) == int(clf.predict([features_to_vector(f)])[0])
        for f in check_rows
    )
    print(f"rules JSON agrees with the sklearn tree on {agree}/{len(check_rows)} series")
    assert agree == len(check_rows), "exported rules diverge from the sklearn tree"

    used_features = _split_features(rules)
    scale_features = [f for f in used_features if f in ("n_obs", "mean_level")]
    limitations = [
        "Trained and evaluated on synthetic series only (generate_diverse_series); no real facility data.",
        f"Labels come from {LABEL_CV_HORIZON}-day rolling-origin CV windows, but the live forecast horizon is 30 days.",
        f"{len(scale_features)} of the tree's {len(used_features)} split features ({', '.join(scale_features)}) are set directly by the "
        "generator's own length and baseline parameters, so the tree can lean on them in a way a real population may not support.",
        "No held-out evidence exists below 46 days of history: length-45 series cannot form a cross-validation window, so all 14 (8 eval, 6 gate) were excluded. Production's SARIMAX floor is 30 days (SARIMAX_MIN_DAYS_REQUIRED), so the deployed selector issues recommendations for 30-45 days of history with no evaluation behind them.",
        "Footnote, not a robustness finding: in 1 of 52 held-out series the fixed default order hit this harness's 100-iteration cap (seed 1307257252, 46 days of history); it converges at production's 200 iterations with the Nelder-Mead-then-L-BFGS retry, so it is not a deployed failure mode. The AI order scored on all 52, but one harness-cap case does not show the AI order is more robust.",
        "Production series are not all observed, and every evaluation series was: as of 2026-10-28, 25 of Northside's 452 modeled days (5.5%, the same for each of its 8 blood types) are carried forward from the last recorded value rather than observed. They are Oct 1-25, 2026, before the nightly snapshot job existed (first run Oct 27, 2026); until then snapshots were written only on upload or inventory change, so the days cannot be recovered.",
        "Known limitation, left unchanged: as of 2026-10-28, four Northside types (A-, AB+, A+, O+) fall back to the linear trend because the AIC-per-observation soundness guard rejects their fits. That guard compares a fit on the full series with a baseline fitted on a training window that ended 30 or more days before selection, so the difference measures how the data changed, not whether the optimizer failed; the comparison is not valid across windows. Replayed on 2,213 fits (facilities 5 and 158), it rejected 41% (86 of 210) of good fits where a stored baseline applied, and passed 114 of 159 fits that were more than 10 AIC above the best start for the same data because most had no baseline to compare against. The alternatives examined (an absolute Ljung-Box gate; a recomputed or age-scaled baseline) did not separate the groups either, so the guard was not changed.",
        "Mechanism of those rejections: the Oct 1-25 carry-forward delivers 26 days of real change (Sep 30 to Oct 26) to the model as a single-day step on Oct 26. Across the 8 Northside types, the size of that step relative to the type's usual 26-day variability has rank correlation 0.93 with the degradation in fit (n = 8, so indicative only).",
        "First real-data test of the AI-recommended order (2026-10-28): it forecast worse than the fixed default. Method: the production fit function, rolling origins every 3 days, horizons 5 to 30 days, scored on observed days only. On all 16 series (facility 158 and Northside, 8 blood types each) the tree's order was (0,1,4)x(0,0,0,7), which is the fixed default (0,1,4)x(1,0,1,7) without its weekly seasonal term, so that term is the only difference between the two. Facility 158 (31 origins, 155 observed days): pooled RMSE ratio AI/default 1.18 (95% CI 1.16 to 1.24, moving-block bootstrap over origins); the AI order had the lower RMSE in 4 of 48 (type, horizon) cells and the smaller absolute error in 23% of 1,256 forecasts. Northside, where production uses grid-searched orders so this is a comparison only: ratio 1.08 (95% CI 1.06 to 1.15), lower RMSE in 5 of 48 cells. All 16 series have a weekday pattern (one-way ANOVA of daily changes by weekday, p near 0, eta^2 0.15 to 0.58 on data through 2026-09-30), and the ratio has rank correlation 0.82 with eta^2 (n = 16, indicative only). Limits: 22 to 30 forecasts per type and horizon on facility 158, the 8 types of one facility are not independent, and only the tree's non-seasonal leaf (0,1,4)x(0,0,0,7) was backtested; its seasonal leaves were not.",
        "Consequence, added 2026-10-28: a weekday-pattern guard in _resolve_order. An AI order with no seasonal term is refused, and the fixed default used, when a one-way ANOVA of the series' daily changes by weekday is significant at 0.05; each refusal is logged with the facility, blood type, order and eta^2. Run on the live series with the committed rules, the guard refuses the tree's order for all 8 of facility 158's blood types and, with Northside's grid-searched rows set aside, for all 8 of Northside's. As of 2026-10-28 facility 158 is the only live consumer of the AI order (Northside's types have grid-searched rows and no other facility has 30 days of history), so the selector currently declines to act on live data. A seasonal AI order is not guarded because none was backtested. The Oct 1-25 carry-forward also bears on this: on facility 158 it roughly halves the weekly feature the tree reads (acf7_diff 0.50 to 0.26 for A+), which moves A+ and B- from a seasonal leaf to a non-seasonal one.",
        "Known limitation, documented rather than changed (2026-10-31): MIN_DAYS_REQUIRED is 3, so a blood bank with only 3 recorded days is given a linear-trend forecast and a restock recommendation (an order of N units by a date) on that history. This is the same weakness the Dashboard's At risk reading is gated for: below SARIMAX_MIN_DAYS_REQUIRED (30 days) the 95% prediction interval is wider than the stock itself (read-only measurement, 2026-10-31, on the four seeded banks with 6 to 8 snapshot days: the 30-day interval was 48 to 334 units wide, wider than current stock for all 32 types, and the lowest lower bound in the window was 0 for each of them), so below 30 days a type is never asserted safe or at risk (asserting safety without a basis is worse than asserting risk without one): it reads 'Not enough history (N of 30 days)' instead of 'On track' or 'At risk'. Recommendations are NOT gated, but each one from a type below 30 days is marked 'Limited history' with its day count, so below 30 days the outlook reports either a recommendation marked as limited-history or 'Not enough history', never 'On track'. Whether recommendations should be withheld below some floor is a larger question, left open.",
        "Known limitation (2026-10-31): the soundness guard's threshold (0.05 AIC per observation over the stored baseline) is tight enough that a type can cross it from one day to the next, so a type's forecast method (SARIMAX or linear trend) can change overnight with nothing about the facility changing. Reported by Euhan, not re-measured: Northside's O+ was rejected on 2026-10-29 at +0.0530 against the 0.05 threshold. The guard keeps no history, so that figure cannot be reproduced after the fact. A read-only check on 2026-10-31 found a complete SARIMAX cache set for O+ trained through 2026-10-31, consistent with it passing today. So the entry above naming four Northside types on the linear trend is a dated statement (2026-10-28/29), not a standing one: on 2026-10-31 A+, A-, and AB+ had no cached SARIMAX forecast (cause not established) and the other five did. The guard is unchanged.",
    ]
    stamp = {
        "deploy_recommended": deploy_recommended, "gate_passed": gate_passed,
        "stage2_passed": passed, "forced_past_gate": force and not gate_passed,
        "gate_reasons": gate_reasons or [],
        "limitations": limitations,
        "split_features": used_features,
        # provenance: everything needed to reproduce or to cite this artifact
        "training": {
            "quick": QUICK, "random_seed": RANDOM_SEED,
            "seed_offsets": {"gate": GATE_SEED_OFFSET, "train": TRAIN_SEED_OFFSET, "eval": EVAL_SEED_OFFSET},
            "n_gate_series": N_GATE_SERIES, "n_train_series": N_TRAIN_SERIES, "n_eval_series": N_EVAL_SERIES,
            "n_train_labeled": len(train_rows), "n_eval_scored": len(eval_rows),
            "label_cv_horizon": LABEL_CV_HORIZON, "label_cv_origins": QUICK_LABEL_CV_ORIGINS if QUICK else LABEL_CV_ORIGINS,
            "eval_horizon": EVAL_HORIZON, "tree_max_depth": TREE_MAX_DEPTH, "tree_min_samples_leaf": TREE_MIN_SAMPLES_LEAF,
            "data": "synthetic (generate_diverse_series); no real facility data",
            "trained_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wall_clock_seconds": round(time.time() - run_started, 1) if run_started is not None else None,
            "sklearn_version": sklearn.__version__,
        },
    }
    # ai/default/oracle at the top level are the PAIRED numbers (the primary result); each method's
    # mean over whichever series it alone scored sits beside them under "unpaired".
    summary = {**paired, "unpaired": {"ai": ai_summary, "default": default_summary, "oracle": oracle_summary}}
    bundle = {
        "tree": clf,
        "feature_names": FEATURE_NAMES,
        "candidate_library": CANDIDATE_LIBRARY,
        "default_index": SARIMAX_DEFAULT_INDEX,
        "evaluation_summary": summary,
        **stamp,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_dir / MODEL_PATH.name)
    rules_path = out_dir / "sarimax_selector_rules.json"
    rules_path.write_text(json.dumps({
        "tree": rules,
        "feature_names": FEATURE_NAMES,
        "candidate_library": CANDIDATE_LIBRARY,
        "default_index": SARIMAX_DEFAULT_INDEX,
        "evaluation_summary": summary,
        **stamp,
    }, indent=2), encoding="utf-8")
    print(f"saved {out_dir / MODEL_PATH.name}\nsaved {rules_path}")
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force-stage2", action="store_true", help="run Stage 2 even if the Stage 1 gate fails")
    parser.add_argument("--quick", action="store_true", help="much smaller N_* constants (demo run, not evidence)")
    parser.add_argument("--out-dir", type=Path, default=MODEL_PATH.parent, help="where artifacts are written")
    args = parser.parse_args()

    if args.quick:
        global N_GATE_SERIES, N_TRAIN_SERIES, N_EVAL_SERIES, QUICK
        QUICK = True
        N_GATE_SERIES, N_TRAIN_SERIES, N_EVAL_SERIES = QUICK_N_GATE, QUICK_N_TRAIN, QUICK_N_EVAL
        print(f"--quick: N_GATE/TRAIN/EVAL = {N_GATE_SERIES}/{N_TRAIN_SERIES}/{N_EVAL_SERIES} (demo run — too small to be evidence)")

    run_started = time.time()  # wall-clock covers the gate and stage 2, recorded in the rules JSON
    gate_passed, gate_reasons = run_stage1_gate(force=args.force_stage2)
    if not gate_passed and not args.force_stage2:
        return
    run_stage2_train_and_evaluate(args.force_stage2, gate_passed, gate_reasons, args.out_dir, run_started)


if __name__ == "__main__":
    main()
