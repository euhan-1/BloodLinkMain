"""Shared, DB-free building blocks for the AI-assisted SARIMAX
model-selection feature: the candidate order library, plain-statistics
feature extraction, and the artifact load/predict helpers.

Imported by BOTH train_sarimax_selector.py (offline training) and main.py
(request-time inference) so the exact same feature computation runs at
training time and at inference time — two separate implementations that
drift apart would silently corrupt the tree's learned splits on real data.
This module never touches the database and never imports main.py (main.py
imports this, not the other way around).
"""

import warnings
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import acf, adfuller, pacf
from statsmodels.tsa.statespace.sarimax import SARIMAX

# ─── Candidate config library ───────────────────────────────────────────
# 12 curated (order, seasonal_order) pairs spanning a real range of
# differencing, AR/MA complexity, and seasonal strength — a deliberately
# small, inspectable set for the tree to choose between, not an exhaustive
# grid search. Index SARIMAX_DEFAULT_INDEX is the already-validated default
# from SYNTHETIC_SARIMAX_VALIDATION.md / fit_and_cache_synthetic_forecast.py
# — always included, so the tree can also just confirm the existing default
# is right for a given series, not only override it.
CANDIDATE_LIBRARY: list[tuple[tuple[int, int, int], tuple[int, int, int, int]]] = [
    ((0, 1, 1), (0, 0, 0, 7)),   # 0 — light MA, no seasonality
    ((0, 1, 4), (0, 0, 0, 7)),   # 1 — moderate MA, no seasonality
    ((1, 1, 1), (0, 0, 0, 7)),   # 2 — AR+MA, no seasonality
    ((0, 1, 1), (1, 0, 0, 7)),   # 3 — light MA + seasonal AR
    ((0, 1, 4), (1, 0, 0, 7)),   # 4 — moderate MA + seasonal AR
    ((0, 1, 4), (1, 0, 1, 7)),   # 5 — VALIDATED DEFAULT
    ((1, 1, 1), (1, 0, 1, 7)),   # 6 — AR+MA + full seasonal
    ((0, 1, 2), (1, 0, 1, 7)),   # 7 — lighter MA + full seasonal
    ((1, 1, 0), (1, 0, 0, 7)),   # 8 — pure AR + seasonal AR
    ((0, 2, 2), (0, 0, 0, 7)),   # 9 — 2nd-order differencing, no seasonality (strong trend/level shifts)
    ((2, 1, 2), (1, 0, 1, 7)),   # 10 — richer AR+MA + full seasonal
    ((0, 1, 1), (1, 1, 1, 7)),   # 11 — seasonal differencing (strong deterministic weekly cycle)
]
SARIMAX_DEFAULT_INDEX = 5
SARIMAX_DEFAULT_ORDER, SARIMAX_DEFAULT_SEASONAL_ORDER = CANDIDATE_LIBRARY[SARIMAX_DEFAULT_INDEX]
SARIMAX_DEFAULT_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"

FEATURE_NAMES = [
    "n_obs", "mean_level", "cv", "adf_stat", "adf_pvalue", "d_suggested",
    "acf1_diff", "acf7_diff", "pacf1_diff", "trend_r2", "trend_slope_norm",
    "seasonal_strength", "noise_to_signal",
]


def _difference_until_stationary(values: np.ndarray, max_d: int = 2, alpha: float = 0.05) -> tuple[np.ndarray, int, float, float]:
    """Same method as identify_synthetic_forecast_data.py's
    _difference_until_stationary — first-differences repeatedly until ADF
    rejects the unit-root null, capped at max_d. Returns
    (final_series, d, raw_adf_stat, raw_adf_pvalue) — the raw (undifferenced)
    ADF result is what's reported as a feature, `d` and the final
    differenced series are used for the ACF/PACF features below."""
    current = values
    # adfuller itself raises on a too-short or constant series rather than
    # returning a degenerate result — a real possibility here since this
    # function must never crash regardless of how little history the caller
    # has (down to whatever SARIMAX_MIN_DAYS_REQUIRED's floor actually is,
    # and callers in tests probe even shorter). Below this floor, "unknown
    # stationarity" (raw_pvalue=1.0, not stationary) is a more honest
    # feature value than propagating an exception.
    if len(current) < 8 or np.ptp(current) == 0:
        return current, 0, 0.0, 1.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, pvalue, *_ = adfuller(current, autolag="AIC")
        raw_stat, raw_pvalue = float(stat), float(pvalue)
        d = 0
        while pvalue >= alpha and d < max_d and len(current) > 10:
            current = np.diff(current)
            d += 1
            if len(current) < 8 or np.ptp(current) == 0:
                break
            stat, pvalue, *_ = adfuller(current, autolag="AIC")
    return current, d, raw_stat, raw_pvalue


def extract_series_features(values) -> dict[str, float]:
    """Plain statistics only — this is NOT the AI, it's what feeds it. A
    daily, regularly-spaced series in (a plain list/array of values, no
    dates needed — period-7 seasonality is about position in a regular
    sequence, not calendar dates), a feature vector out. Reuses the same
    ADF/differencing approach as identify_synthetic_forecast_data.py and the
    same acf/pacf calls as that script.
    """
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    mean_level = float(arr.mean())
    std_level = float(arr.std())
    cv = std_level / mean_level if mean_level > 0 else 0.0

    diffed, d, adf_stat, adf_pvalue = _difference_until_stationary(arr)

    # acf's only constraint is nlags <= len(diffed)-1; pacf is stricter —
    # nlags must be < len(diffed)//2 (statsmodels raises otherwise) — so the
    # two need separate caps, not one shared max_lag.
    if len(diffed) > 8:
        acf_max_lag = min(14, len(diffed) - 1)
        acf_vals = acf(diffed, nlags=acf_max_lag, fft=True)
    else:
        acf_vals = np.zeros(8)
    pacf_max_lag = min(14, len(diffed) // 2 - 1)
    if len(diffed) > 8 and pacf_max_lag >= 1:
        pacf_vals = pacf(diffed, nlags=pacf_max_lag)
    else:
        pacf_vals = np.zeros(8)
    acf1 = float(acf_vals[1]) if len(acf_vals) > 1 else 0.0
    acf7 = float(acf_vals[7]) if len(acf_vals) > 7 else 0.0
    pacf1 = float(pacf_vals[1]) if len(pacf_vals) > 1 else 0.0

    # Trend strength: R^2 of an OLS linear fit on the raw series. Computed
    # once, unconditionally (with a safe flat-line fallback), so the
    # seasonal-strength block below can reuse the same detrending.
    x = np.arange(n, dtype=float)
    if n >= 3 and std_level > 0:
        slope, intercept = np.polyfit(x, arr, 1)
        fitted = slope * x + intercept
        ss_res = float(np.sum((arr - fitted) ** 2))
        ss_tot = float(np.sum((arr - mean_level) ** 2))
        trend_r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        trend_slope_norm = float(slope) / mean_level if mean_level > 0 else 0.0
    else:
        slope, intercept = 0.0, mean_level
        trend_r2, trend_slope_norm = 0.0, 0.0

    # Seasonal strength (period 7): variance explained by a naive seasonal
    # profile (mean value at each position mod 7 of the detrended series)
    # vs the detrended series' total variance — a classical
    # decomposition-based strength measure (0 = no period-7 pattern, closer
    # to 1 = most of the variance is a period-7 pattern), not a learned one.
    if n >= 14:
        detrended = arr - (slope * x + intercept)
        positions = np.arange(n) % 7
        seasonal_profile = np.array([detrended[positions == p].mean() for p in range(7)])
        seasonal_component = seasonal_profile[positions]
        resid = detrended - seasonal_component
        var_detrended = float(np.var(detrended))
        var_resid = float(np.var(resid))
        seasonal_strength = max(0.0, 1 - var_resid / var_detrended) if var_detrended > 0 else 0.0
        noise_to_signal = float(np.std(resid) / std_level) if std_level > 0 else 0.0
    else:
        seasonal_strength, noise_to_signal = 0.0, 1.0

    return {
        "n_obs": float(n),
        "mean_level": mean_level,
        "cv": float(cv),
        "adf_stat": float(adf_stat),
        "adf_pvalue": float(adf_pvalue),
        "d_suggested": float(d),
        "acf1_diff": acf1,
        "acf7_diff": acf7,
        "pacf1_diff": pacf1,
        "trend_r2": float(trend_r2),
        "trend_slope_norm": float(trend_slope_norm),
        "seasonal_strength": float(seasonal_strength),
        "noise_to_signal": float(noise_to_signal),
    }


def features_to_vector(features: dict[str, float]) -> list[float]:
    return [features[name] for name in FEATURE_NAMES]


def fit_candidate(
    endog: pd.Series, exog: pd.DataFrame,
    order: tuple[int, int, int], seasonal_order: tuple[int, int, int, int],
    maxiter: int = 100,
) -> Optional[dict]:
    """Fits one candidate SARIMAX config and reports AIC/BIC — the same
    fit-and-compare building block estimate_synthetic_forecast_models.py
    uses for its (non-seasonal) candidate list, extended with a seasonal
    order. Returns None (never raises) on any fit failure or
    non-convergence, exactly like _fit_sarimax_facility_forecast in main.py.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(
                endog, exog=exog, order=order, seasonal_order=seasonal_order, trend=None,
                enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False, maxiter=maxiter)
        if not fit.mle_retvals.get("converged", False):
            return None
        if not (np.isfinite(fit.aic) and np.isfinite(fit.bic)):
            return None
    except Exception:
        return None
    return {"aic": float(fit.aic), "bic": float(fit.bic), "fit": fit}


def best_candidate_index(
    endog: pd.Series, exog: pd.DataFrame, library=CANDIDATE_LIBRARY, maxiter: int = 100
) -> Optional[int]:
    """Fits every candidate in `library` and returns the index of the
    best-AIC one. NOT used as the training-label rule as of the second
    training attempt — a first attempt used this (best-AIC) as the label and
    hit its own STOP condition: even the best-AIC oracle didn't beat the
    fixed default on held-out forecast accuracy, i.e. AIC-best-fit-in-sample
    isn't the same thing as best-out-of-sample-forecast, and training a
    classifier to predict a label that itself doesn't track the metric that
    actually matters can't produce something useful. Kept here as a general
    utility (still a legitimate "which config fits this data best,
    statistically" question) — see best_candidate_by_forecast_error below
    for the rule actually used to generate labels now.
    None if every single candidate failed to fit."""
    best_idx, best_aic = None, None
    for i, (order, seasonal_order) in enumerate(library):
        result = fit_candidate(endog, exog, order, seasonal_order, maxiter=maxiter)
        if result is None:
            continue
        if best_aic is None or result["aic"] < best_aic:
            best_aic, best_idx = result["aic"], i
    return best_idx


def _forecast_errors(fit, test_endog: pd.Series, test_exog: pd.DataFrame) -> Optional[dict]:
    """Scores one already-fitted model's forecast against real held-out
    values. Returns None (never raises) if the forecast call itself fails or
    produces any non-finite value — a candidate whose forecast blows up is
    disqualified, not scored with a huge-but-finite number that could still
    accidentally look competitive."""
    horizon = len(test_endog)
    try:
        fc = fit.get_forecast(steps=horizon, exog=test_exog).predicted_mean.values
    except Exception:
        return None
    if not np.all(np.isfinite(fc)):
        return None
    actual = test_endog.values
    pct_err = np.abs((actual - fc) / np.where(actual == 0, np.nan, actual))
    mape = float(np.nanmean(pct_err) * 100) if np.any(np.isfinite(pct_err)) else None
    if mape is None:
        return None
    return {"rmse": float(np.sqrt(np.mean((actual - fc) ** 2))), "mape": mape}


def rolling_origin_windows(n: int, horizon: int, n_origins: int, step: Optional[int] = None) -> list[int]:
    """Up to `n_origins` split points (0-based indices) spaced `step` apart
    (default: `horizon`), working backward from n - horizon, each requiring
    at least `horizon` days of training history before it. Returns fewer
    than n_origins — down to an empty list — if the series is too short;
    never raises. Same rolling-origin design as
    crossvalidate_synthetic_forecast_model.py, just parametrized instead of
    hard-coded to that script's specific series length."""
    step = step or horizon
    origins = []
    origin = n - horizon
    while origin >= horizon and len(origins) < n_origins:
        origins.append(origin)
        origin -= step
    return list(reversed(origins))


def best_candidate_by_forecast_error(
    endog: pd.Series, exog: pd.DataFrame, library=CANDIDATE_LIBRARY,
    horizon: int = 14, n_origins: int = 2, maxiter: int = 60,
) -> Optional[tuple[int, dict[int, float]]]:
    """THE label/selection rule actually used now: rolling-origin
    cross-validated forecast error (mean MAPE across up to `n_origins`
    origins within `endog`), not AIC/BIC — this targets the metric a
    forecast is actually judged on, not in-sample fit quality. Also reused
    as the "full search" / oracle-ish reference at evaluation time (see
    train_sarimax_selector.py) — expensive (fits every candidate at every
    origin) but principled, and exactly what a human doing this by hand
    with a spreadsheet of forecast errors would do.

    A candidate that fails to fit or forecast at ANY attempted origin is
    disqualified entirely for this series, not scored on however many
    origins it did manage — an unreliable config is worse than a
    consistently-average one.

    Returns (best_index, {index: mean_mape}) for every surviving candidate,
    or None if the series is too short to form even one origin, or every
    candidate was disqualified.
    """
    n = len(endog)
    origins = rolling_origin_windows(n, horizon, n_origins)
    if not origins:
        return None

    disqualified: set[int] = set()
    scores: dict[int, list[float]] = {i: [] for i in range(len(library))}

    for origin in origins:
        train_endog, train_exog = endog.iloc[:origin], exog.iloc[:origin]
        test_endog, test_exog = endog.iloc[origin:origin + horizon], exog.iloc[origin:origin + horizon]
        for i, (order, seasonal_order) in enumerate(library):
            if i in disqualified:
                continue
            fit_result = fit_candidate(train_endog, train_exog, order, seasonal_order, maxiter=maxiter)
            if fit_result is None:
                disqualified.add(i)
                continue
            errors = _forecast_errors(fit_result["fit"], test_endog, test_exog)
            if errors is None:
                disqualified.add(i)
                continue
            scores[i].append(errors["mape"])

    valid = {i: v for i, v in scores.items() if i not in disqualified and len(v) == len(origins)}
    if not valid:
        return None
    mean_mape = {i: float(np.mean(v)) for i, v in valid.items()}
    best_idx = min(mean_mape, key=mean_mape.get)
    return best_idx, mean_mape


DEFAULT_MODEL_PATH = Path(__file__).parent / "models" / "sarimax_selector.joblib"

_loaded_model_cache: dict[str, dict] = {}


def load_selector_model(path: Path = DEFAULT_MODEL_PATH) -> dict:
    """Loads the saved model bundle once and caches it in this process's
    memory — callers must not reload per request (see main.py's
    module-level SARIMAX_SELECTOR_BUNDLE). Raises on any problem (missing
    file, corrupt artifact); the caller is responsible for catching that and
    falling back to the fixed default, same as any other SARIMAX fit
    failure elsewhere in this app.

    The bundle carries its OWN copy of the candidate library it was trained
    against (bundle["candidate_library"]), not just the tree — so a
    predicted index is always resolved against what the tree actually
    learned on, even if this module's live CANDIDATE_LIBRARY constant is
    later edited without retraining.
    """
    key = str(path)
    if key not in _loaded_model_cache:
        _loaded_model_cache[key] = joblib.load(path)
    return _loaded_model_cache[key]


def recommend_candidate_index(features: dict[str, float], bundle: dict) -> int:
    tree = bundle["tree"]
    vector = [features_to_vector(features)]
    return int(tree.predict(vector)[0])
