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
"""

import random
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.tree import DecisionTreeClassifier, export_text

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
)

DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}  # must match main.py's DENGUE_SEASON_MONTHS

RANDOM_SEED = 11
N_GATE_SERIES = 70  # Stage 1 gate sample
N_TRAIN_SERIES = 220
N_EVAL_SERIES = 60

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


def _process_one_series(seed: int, params: dict, need_features: bool) -> Optional[dict]:
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
        horizon=LABEL_CV_HORIZON, n_origins=LABEL_CV_ORIGINS, maxiter=LABEL_FIT_MAXITER,
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
    }
    if need_features:
        result["features"] = extract_series_features(hist_values)
    return result


def _run_batch(n_series: int, seed_offset: int, need_features: bool) -> list[dict]:
    rng = random.Random(RANDOM_SEED + seed_offset)
    param_sets = [sample_series_params(rng) for _ in range(n_series)]
    seeds = [rng.randint(0, 2**31 - 1) for _ in range(n_series)]
    t0 = time.time()
    results = Parallel(n_jobs=-1, verbose=5)(
        delayed(_process_one_series)(seed, params, need_features) for seed, params in zip(seeds, param_sets)
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


def run_stage1_gate() -> tuple[bool, list[dict]]:
    print(f"=== STAGE 1 GATE: oracle (forecast-error-selected config) vs fixed default, {N_GATE_SERIES} heterogeneous series ===")
    rows = _run_batch(N_GATE_SERIES, seed_offset=500, need_features=False)
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

    if not passed:
        print("\nSTOP at Stage 1: the premise doesn't hold on this heterogeneous, non-rigged population.")
        if not (beats_default_rmse and beats_default_mape):
            print("The oracle (full information, forecast-error selection) doesn't clearly beat the fixed default.")
        elif not meaningful_margin:
            print(f"The oracle beats the default, but only marginally (RMSE {rmse_margin_pct:+.1f}%, MAPE {mape_margin_pct:+.1f}%) — not a meaningful margin.")
        if not varies:
            print(f"The winning config barely varies ({len(label_counts)} distinct) — most series want the same config regardless of their real differences.")
        print("Per-facility config selection does not appear to genuinely help on this data. NOT training a classifier against this target.")
    else:
        print(f"\nGATE PASSED: oracle beats default by a meaningful margin (RMSE {rmse_margin_pct:+.1f}%, MAPE {mape_margin_pct:+.1f}%) and the winning config varies ({len(label_counts)} distinct). Proceeding to Stage 2.")

    return passed, rows


def run_stage2_train_and_evaluate():
    print(f"\n=== STAGE 2: generating + labeling {N_TRAIN_SERIES} training series (forecast-error rule) ===")
    train_rows = _run_batch(N_TRAIN_SERIES, seed_offset=1000, need_features=True)
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
    eval_rows = _run_batch(N_EVAL_SERIES, seed_offset=9000, need_features=True)
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

    print("\n=== EVALUATION RESULTS (held-out synthetic series, final-horizon forecast error) ===")
    print(f"{'method':<24}{'n':>5}{'RMSE':>10}{'MAPE%':>10}{'avg time/series (s)':>22}")
    print(f"{'AI-recommended':<24}{ai_summary['n']:>5}{ai_summary['rmse']:>10.3f}{ai_summary['mape']:>10.2f}{ai_avg_time:>22.3f}")
    print(f"{'Fixed default':<24}{default_summary['n']:>5}{default_summary['rmse']:>10.3f}{default_summary['mape']:>10.2f}{default_avg_time:>22.3f}")
    print(f"{'Oracle (full CV search)':<24}{oracle_summary['n']:>5}{oracle_summary['rmse']:>10.3f}{oracle_summary['mape']:>10.2f}{oracle_avg_time:>22.3f}")

    ai_variety = len(set(r["idx"] for r in ai_results))
    ai_distribution = pd.Series([r["idx"] for r in ai_results]).value_counts()
    print(f"\nAI recommended {ai_variety} distinct configs across {len(ai_results)} held-out series:")
    for idx, count in ai_distribution.items():
        print(f"  [{idx}] {CANDIDATE_LIBRARY[idx]}: recommended for {count} series")

    beats_default_rmse = ai_summary["rmse"] < default_summary["rmse"]
    beats_default_mape = ai_summary["mape"] < default_summary["mape"]
    varies_meaningfully = ai_variety >= 3

    print("\n=== STAGE 2 STOP CONDITION CHECK ===")
    print(f"AI beats fixed default on RMSE: {beats_default_rmse}  ({ai_summary['rmse']:.3f} vs {default_summary['rmse']:.3f})")
    print(f"AI beats fixed default on MAPE: {beats_default_mape}  ({ai_summary['mape']:.2f}% vs {default_summary['mape']:.2f}%)")
    print(f"AI recommendations vary meaningfully (>=3 distinct configs): {varies_meaningfully}  ({ai_variety} distinct)")

    passed = varies_meaningfully and (beats_default_rmse or beats_default_mape)
    if not passed:
        print("\nSTOP at Stage 2: the trained classifier does not clear the bar. NOT saving a model artifact, NOT integrating.")
        return None

    print("\nSTAGE 2 PASSED: saving model artifact.")
    bundle = {
        "tree": clf,
        "feature_names": FEATURE_NAMES,
        "candidate_library": CANDIDATE_LIBRARY,
        "default_index": SARIMAX_DEFAULT_INDEX,
        "evaluation_summary": {"ai": ai_summary, "default": default_summary, "oracle": oracle_summary},
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    print(f"saved {MODEL_PATH}")
    return bundle


def main():
    gate_passed, _gate_rows = run_stage1_gate()
    if not gate_passed:
        return
    run_stage2_train_and_evaluate()


if __name__ == "__main__":
    main()
