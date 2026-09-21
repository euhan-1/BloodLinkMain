"""Trains and evaluates the AI-assisted SARIMAX model-selection classifier —
the emerging-technology piece that replaces the hardcoded
SARIMAX(0,1,4)x(1,0,1,7) default with a per-facility, per-blood-type choice
from a curated candidate library (sarimax_selector_common.CANDIDATE_LIBRARY).

SARIMAX still produces every forecast number; the classifier only ever picks
WHICH config SARIMAX fits with. It never predicts raw parameters or touches
a forecast value.

Pipeline (reusing the existing *_synthetic_forecast_*.py pipeline's own
building blocks, not reinventing them):
  1. Generate many diverse synthetic daily series in memory — deliberately
     varying seasonal strength, trend, noise, level, and length (unlike
     generate_synthetic_forecast_data.py's 8 fixed series, one per real
     blood type) so different candidates genuinely win for different series.
  2. Label each series with the Box-Jenkins rule this classifier replaces:
     fit every candidate in the library, the best AIC wins — exactly
     estimate_synthetic_forecast_models.py's own selection rule
     (sarimax_selector_common.best_candidate_index), just applied to a
     bigger, seasonal-order-aware library.
  3. Extract plain-statistics features per series
     (sarimax_selector_common.extract_series_features — ADF, ACF/PACF,
     trend/seasonal strength, same methods identify_synthetic_forecast_data.py
     already uses).
  4. Train a shallow, interpretable DecisionTreeClassifier on
     (features -> best-candidate-index).
  5. Evaluate on a held-out set never seen during training: AI-recommended
     config vs the fixed default vs a full Box-Jenkins search, on forecast
     accuracy (MAPE/RMSE) and selection time. This is the thesis result.
  6. STOP CONDITION, checked automatically, not assumed: if the tree's
     recommendations barely vary across series, or it doesn't beat the fixed
     default, this prints a STOP verdict and does NOT save the model. Only a
     GO verdict writes models/sarimax_selector.joblib.

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
    best_candidate_index,
    extract_series_features,
    features_to_vector,
    fit_candidate,
)

DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}  # must match main.py's DENGUE_SEASON_MONTHS

RANDOM_SEED = 7
N_TRAIN_SERIES = 220
N_EVAL_SERIES = 60
LABEL_FIT_MAXITER = 60  # lower than main.py's request-time 200 — fine for AIC ranking, matches this pipeline's existing precedent (estimate_synthetic_forecast_models.py doesn't set maxiter at all, defaulting to statsmodels' ~50)
EVAL_FIT_MAXITER = 100
EVAL_HORIZON = 14  # forecast this many held-out days per eval series, same operational horizon as crossvalidate_synthetic_forecast_model.py

TREE_MAX_DEPTH = 4
TREE_MIN_SAMPLES_LEAF = 12

MODEL_PATH = Path(__file__).parent / "models" / "sarimax_selector.joblib"

DATE_POOL_START = date(2022, 1, 1)
DATE_POOL_END = date(2025, 6, 1)  # leaves room for length up to 400 + horizon before "today"


def _dengue_flag(dates: list[date]) -> np.ndarray:
    return np.array([1.0 if d.month in DENGUE_SEASON_MONTHS else 0.0 for d in dates])


def generate_diverse_series(rng: random.Random, length: int, baseline: float, dengue_trough_mult: float,
                             trend_frac: float, noise_frac: float, weekly_amp: float,
                             start_date: date) -> tuple[list[date], np.ndarray]:
    """Same mean-reverting level + periodic-restock + noise mechanics as
    generate_synthetic_forecast_data.py's generate_series, but parametrized
    on 5 independent knobs (seasonal depth, trend, noise, weekly-pattern
    strength, baseline level) instead of that script's fixed per-type
    values — deliberately, so different candidates in the library genuinely
    win for different series and the tree has real signal to learn from."""
    dates = [start_date + timedelta(days=i) for i in range(length)]
    dengue = _dengue_flag(dates)

    # weekly_amp controls the PROBABILITY each restock lands on an exact
    # 7-day cadence vs. a random 5-9 day one — not a small ripple layered on
    # top of random restock timing. An earlier version tried the ripple
    # approach (modulating the draw rate, then a direct additive bump on the
    # observed value) and neither produced a detectable period-7 pattern:
    # verified via a standalone diagnostic that seasonal_strength/acf7_diff
    # stayed within noise of each other at weekly_amp=0 vs 1.0 either way —
    # the random-interval restock's own large jumps (target x 1.15-1.35,
    # far bigger than any plausible ripple) dominated the series' variance
    # and swamped a smaller added signal. Locking the restock cadence itself
    # is both what actually produces a clean, learnable weekly signal AND a
    # more realistic story (weekly_amp=1 = a facility whose replenishment
    # genuinely runs on a fixed weekly donation-drive schedule; weekly_amp=0
    # = one with no such regular cadence at all).
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


def _label_one_series(seed: int, params: dict) -> dict:
    """Picklable top-level worker for joblib.Parallel: generates one series,
    labels it via the full-library best-AIC search, extracts features.
    Returns None if every candidate failed to fit (rare, but a series can be
    pathological — dropped rather than mislabeled)."""
    rng = random.Random(seed)
    dates, values = generate_diverse_series(rng, **params)

    idx = pd.DatetimeIndex(dates)
    endog = pd.Series(values, index=idx, dtype=float)
    exog = pd.DataFrame({"dengue_season": _dengue_flag(dates)}, index=idx)

    label = best_candidate_index(endog, exog, maxiter=LABEL_FIT_MAXITER)
    if label is None:
        return None

    features = extract_series_features(values)
    return {"features": features, "label": label, "params": params}


def build_labeled_dataset(n_series: int, seed_offset: int) -> list[dict]:
    rng = random.Random(RANDOM_SEED + seed_offset)
    param_sets = [sample_series_params(rng) for _ in range(n_series)]
    seeds = [rng.randint(0, 2**31 - 1) for _ in range(n_series)]

    t0 = time.time()
    results = Parallel(n_jobs=-1, verbose=5)(
        delayed(_label_one_series)(seed, params) for seed, params in zip(seeds, param_sets)
    )
    print(f"labeled {n_series} series in {time.time() - t0:.1f}s")
    return [r for r in results if r is not None]


def _evaluate_one_series(seed: int, params: dict, bundle: dict) -> Optional[dict]:
    rng = random.Random(seed)
    dates, values = generate_diverse_series(rng, **params)
    n = len(values)
    if n < 45 + EVAL_HORIZON:
        return None

    split = n - EVAL_HORIZON
    train_dates, test_dates = dates[:split], dates[split:]
    train_values, test_values = values[:split], values[split:]

    idx = pd.DatetimeIndex(train_dates)
    train_endog = pd.Series(train_values, index=idx, dtype=float)
    train_exog = pd.DataFrame({"dengue_season": _dengue_flag(train_dates)}, index=idx)
    test_exog = pd.DataFrame({"dengue_season": _dengue_flag(test_dates)}, index=pd.DatetimeIndex(test_dates))

    features = extract_series_features(train_values)

    def _forecast_with(order, seasonal_order):
        t0 = time.time()
        result = fit_candidate(train_endog, train_exog, order, seasonal_order, maxiter=EVAL_FIT_MAXITER)
        elapsed = time.time() - t0
        if result is None:
            return None, elapsed
        fc = result["fit"].get_forecast(steps=EVAL_HORIZON, exog=test_exog).predicted_mean.values
        return fc, elapsed

    # AI: 1 tree lookup + 1 fit.
    tree = bundle["tree"]
    library = bundle["candidate_library"]
    t0 = time.time()
    ai_idx = int(tree.predict([features_to_vector(features)])[0])
    ai_order, ai_seasonal = library[ai_idx]
    ai_fc, ai_fit_time = _forecast_with(ai_order, ai_seasonal)
    ai_total_time = (time.time() - t0)

    # Fixed default: 1 fit, no selection step at all.
    default_fc, default_time = _forecast_with(SARIMAX_DEFAULT_ORDER, SARIMAX_DEFAULT_SEASONAL_ORDER)

    # Full Box-Jenkins search: fit every candidate, pick best AIC, forecast
    # with it — same rule the training labels use, applied fresh to this
    # held-out series' own history.
    t0 = time.time()
    search_idx = best_candidate_index(train_endog, train_exog, library=library, maxiter=EVAL_FIT_MAXITER)
    search_time = time.time() - t0
    if search_idx is not None:
        search_order, search_seasonal = library[search_idx]
        search_fc, _ = _forecast_with(search_order, search_seasonal)
    else:
        search_fc = None

    def _errors(fc):
        if fc is None:
            return None
        actual = test_values
        abs_err = np.abs(actual - fc)
        pct_err = np.abs((actual - fc) / np.where(actual == 0, np.nan, actual))
        return {
            "rmse": float(np.sqrt(np.mean((actual - fc) ** 2))),
            "mape": float(np.nanmean(pct_err) * 100),
        }

    return {
        "ai_idx": ai_idx,
        "ai_errors": _errors(ai_fc),
        "ai_time": ai_total_time,
        "default_errors": _errors(default_fc),
        "default_time": default_time,
        "search_idx": search_idx,
        "search_errors": _errors(search_fc),
        "search_time": search_time,
    }


def main():
    print(f"=== Stage 1-3: generating + labeling {N_TRAIN_SERIES} training series ===")
    train_rows = build_labeled_dataset(N_TRAIN_SERIES, seed_offset=1000)
    print(f"{len(train_rows)}/{N_TRAIN_SERIES} series labeled successfully (rest had no converging candidate)")

    label_counts = pd.Series([r["label"] for r in train_rows]).value_counts().sort_index()
    print("\nLabel distribution in training data:")
    for idx, count in label_counts.items():
        order, seasonal = CANDIDATE_LIBRARY[idx]
        print(f"  [{idx}] {order}x{seasonal}: {count} series")

    X_train = [features_to_vector(r["features"]) for r in train_rows]
    y_train = [r["label"] for r in train_rows]

    print(f"\n=== Stage 4: training DecisionTreeClassifier (max_depth={TREE_MAX_DEPTH}) ===")
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH, min_samples_leaf=TREE_MIN_SAMPLES_LEAF, random_state=RANDOM_SEED,
    )
    clf.fit(X_train, y_train)
    train_acc = clf.score(X_train, y_train)
    print(f"training-set accuracy (label match, not the real metric — see evaluation below): {train_acc:.3f}")

    print("\nFeature importances:")
    for name, importance in sorted(zip(FEATURE_NAMES, clf.feature_importances_), key=lambda p: -p[1]):
        print(f"  {name:<20} {importance:.4f}")

    print("\nTree structure:")
    tree_text = export_text(clf, feature_names=FEATURE_NAMES)
    print(tree_text)

    print(f"\n=== Stage 5: evaluating on {N_EVAL_SERIES} held-out series (never used in training) ===")
    bundle = {
        "tree": clf,
        "feature_names": FEATURE_NAMES,
        "candidate_library": CANDIDATE_LIBRARY,
        "default_index": SARIMAX_DEFAULT_INDEX,
    }
    eval_rng = random.Random(RANDOM_SEED + 9999)
    eval_param_sets = [sample_series_params(eval_rng) for _ in range(N_EVAL_SERIES)]
    eval_seeds = [eval_rng.randint(0, 2**31 - 1) for _ in range(N_EVAL_SERIES)]

    eval_results = []
    for i, (seed, params) in enumerate(zip(eval_seeds, eval_param_sets)):
        r = _evaluate_one_series(seed, params, bundle)
        if r is not None:
            eval_results.append(r)
        if (i + 1) % 10 == 0:
            print(f"  evaluated {i + 1}/{N_EVAL_SERIES}")

    def _summarize(key_errors, key_time):
        rows = [r for r in eval_results if r[key_errors] is not None]
        rmse = np.mean([r[key_errors]["rmse"] for r in rows])
        mape = np.mean([r[key_errors]["mape"] for r in rows])
        avg_time = np.mean([r[key_time] for r in eval_results])
        return {"n": len(rows), "rmse": float(rmse), "mape": float(mape), "avg_time_s": float(avg_time)}

    ai_summary = _summarize("ai_errors", "ai_time")
    default_summary = _summarize("default_errors", "default_time")
    search_summary = _summarize("search_errors", "search_time")

    print("\n=== EVALUATION RESULTS (held-out synthetic series) ===")
    print(f"{'method':<20}{'n':>5}{'RMSE':>10}{'MAPE%':>10}{'avg time/series (s)':>22}")
    for name, s in [("AI-recommended", ai_summary), ("Fixed default", default_summary), ("Full Box-Jenkins search", search_summary)]:
        print(f"{name:<20}{s['n']:>5}{s['rmse']:>10.3f}{s['mape']:>10.2f}{s['avg_time_s']:>22.3f}")

    ai_label_variety = len(set(r["ai_idx"] for r in eval_results))
    ai_label_distribution = pd.Series([r["ai_idx"] for r in eval_results]).value_counts()
    print(f"\nAI recommended {ai_label_variety} distinct configs across {len(eval_results)} held-out series:")
    for idx, count in ai_label_distribution.items():
        print(f"  [{idx}] {CANDIDATE_LIBRARY[idx]}: recommended for {count} series")

    beats_default_rmse = ai_summary["rmse"] < default_summary["rmse"]
    beats_default_mape = ai_summary["mape"] < default_summary["mape"]
    varies_meaningfully = ai_label_variety >= 3

    print("\n=== STOP CONDITION CHECK ===")
    print(f"AI beats fixed default on RMSE: {beats_default_rmse}  ({ai_summary['rmse']:.3f} vs {default_summary['rmse']:.3f})")
    print(f"AI beats fixed default on MAPE: {beats_default_mape}  ({ai_summary['mape']:.2f}% vs {default_summary['mape']:.2f}%)")
    print(f"AI recommendations vary meaningfully (>=3 distinct configs): {varies_meaningfully}  ({ai_label_variety} distinct)")

    if not (varies_meaningfully and (beats_default_rmse or beats_default_mape)):
        print("\nSTOP: the feature does not clear the bar set in advance. NOT saving a model artifact.")
        print("Do not wire this into main.py — report this result instead of shipping it.")
        return

    print("\nGO: recommendations vary meaningfully and the AI beats the fixed default. Saving model artifact.")
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    bundle["evaluation_summary"] = {"ai": ai_summary, "default": default_summary, "full_search": search_summary}
    joblib.dump(bundle, MODEL_PATH)
    print(f"saved {MODEL_PATH}")


if __name__ == "__main__":
    main()
