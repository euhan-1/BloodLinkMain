# Phase 4: Forecasting and out-of-sample validation

> **Data notice.** The 180-day series analysed here (`northside_180d_history.csv`, 2026-03-30 to 2026-09-25, 8 blood types, 1,440 daily counts) is **generated demonstration data**, not real blood bank records. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

## What the live system does versus what this report does

Live, `GET /forecast` fits the fixed order on a facility's history and forecasts 30 days ahead, returning **seven
checkpoints only** (days 0, 5, 10, 15, 20, 25, 30), rounded to whole units and floored at zero, with a 95% interval
(`_fit_sarimax_facility_forecast`, `main.py:735`; `FORECAST_INTERVAL_CONFIDENCE = 0.95`, `main.py:54`). Fits are cached per facility, type and day
(`_get_or_fit_cached_sarimax`, `main.py:818`). **The live system has no accuracy monitoring**: it never compares a forecast
with what later happened. The hold-out below is an offline test of the same model.

## Method (code: `run_methodology.py`, phase 4 block)

* Hold out the **last 30 days** (2026-08-27 to 2026-09-25). Fit on the first 150 days (2026-03-30 to 2026-08-26) with the
  live specification and binary dengue regressor. Forecast the 30 held-out days directly, with exogenous values from
  `dengue_season_index` for the future dates, exactly as the live function builds them.
* **Daily** forecasts and 95% intervals are scored (30 points per type), not just the seven live checkpoints.
* Metrics: MAPE, RMSE, MAE on the point forecast. Interval coverage = fraction of the 30 actuals inside the 95% interval.
* **Baselines:** (1) naive: last training value carried forward for all 30 days; (2) seasonal naive: the last observed
  week repeated. Only (1) was required; (2) is added because the data has a weekly pattern.
* **Parity check:** for every type the script also calls the production function `_fit_sarimax_facility_forecast` on the same
  150-day training series and compares its checkpoint forecasts with this script's. Result: **identical for all 8 types at all six forecast checkpoints**.
  So the numbers below are what the live model produces, not a lookalike.

## 1. Accuracy against the naive baseline

Cells are MAPE % / RMSE / MAE (units). Lower is better.

| Type | SARIMAX | Naive (last value) | Seasonal naive (7d) |
|---|---|---|---|
| A+ | 4.22 / 3.35 / 2.80 | 6.72 / 5.43 / 4.57 | 7.63 / 6.14 / 4.93 |
| A- | 4.05 / 0.78 / 0.64 | 5.12 / 1.08 / 0.83 | 6.49 / 1.51 / 1.00 |
| AB+ | 3.23 / 0.94 / 0.67 | 4.23 / 1.21 / 0.93 | 5.00 / 1.34 / 1.07 |
| AB- | 5.61 / 0.48 / 0.41 | 5.71 / 0.63 / 0.40 | 4.70 / 0.58 / 0.33 |
| B+ | 3.81 / 3.41 / 2.82 | 5.44 / 4.88 / 4.13 | 6.90 / 6.66 / 5.10 |
| B- | 5.49 / 1.13 / 0.96 | 7.27 / 1.44 / 1.27 | 5.88 / 1.28 / 1.03 |
| O+ | 6.81 / 9.19 / 6.56 | 15.04 / 17.02 / 16.00 | 8.50 / 10.86 / 8.47 |
| O- | 5.89 / 1.66 / 1.31 | 10.39 / 2.82 / 2.50 | 9.43 / 2.65 / 2.13 |

**SARIMAX vs naive (last value):** SARIMAX has the lower MAPE for 8 of 8 types, the lower RMSE for 8 of 8, and the
lower MAE for 7 of 8. It does not beat naive on MAE for AB- (AB-: 0.41 vs 0.40), a near tie.
The gains are largest where the series fall fastest (O+ MAE 6.6 vs 16.0), which is what a naive
"no change" baseline should do badly on, because these series decline steadily. **That is a weak baseline for this data.**
Against the stronger seasonal-naive baseline, SARIMAX has lower MAE for 7 of 8 types
(A+, A-, AB+, B+, B-, O+, O-); it loses on AB-.

## 2. Prediction-interval coverage

Nominal coverage is 95%.

| Type | Actuals inside 95% interval | Coverage |
|---|---|---|
| A+ | 30/30 | 100.0% |
| A- | 30/30 | 100.0% |
| AB+ | 30/30 | 100.0% |
| AB- | 30/30 | 100.0% |
| B+ | 30/30 | 100.0% |
| B- | 30/30 | 100.0% |
| O+ | 29/30 | 96.7% |
| O- | 30/30 | 100.0% |

Overall **239 of 240 (99.6%)** of held-out actuals fell inside the interval. That is well **above** the nominal 95%:
the intervals are **too wide (conservative)**, not too narrow. Reading this as "well calibrated" would be wrong. It is over-cover,
consistent with Phase 3's variance finding (a constant-variance model fitted on the earlier, higher-variance part of a
declining series). For the dashboard this errs on the safe side but overstates uncertainty.

## 3. How the interval widens over the horizon

Width of the 95% interval (upper minus lower, units) at each horizon day.

| Type | Day 1 | Day 5 | Day 10 | Day 15 | Day 20 | Day 25 | Day 30 | Day 30 / Day 1 |
|---|---|---|---|---|---|---|---|---|
| A+ | 16.4 | 17.5 | 19.6 | 21.6 | 23.4 | 25.3 | 27.2 | 1.65x |
| A- | 4.2 | 4.3 | 4.5 | 4.8 | 4.9 | 5.2 | 5.5 | 1.30x |
| AB+ | 5.3 | 5.8 | 6.4 | 6.9 | 7.4 | 8.0 | 8.5 | 1.60x |
| AB- | 2.0 | 2.2 | 2.3 | 2.5 | 2.7 | 2.8 | 3.0 | 1.50x |
| B+ | 18.4 | 19.4 | 21.1 | 22.8 | 24.3 | 26.2 | 28.1 | 1.53x |
| B- | 4.5 | 4.8 | 5.1 | 5.5 | 5.8 | 6.1 | 6.5 | 1.43x |
| O+ | 36.4 | 42.0 | 45.0 | 46.7 | 48.4 | 50.4 | 52.0 | 1.43x |
| O- | 7.9 | 8.7 | 9.4 | 10.1 | 10.7 | 11.3 | 12.0 | 1.51x |

Widths grow steadily and smoothly with horizon for every type, by roughly 1.3x to 1.7x from day 1 to day 30, the qualitative behaviour
the methodology predicts. They start wide (day-1 width already large relative to the level), which is the over-coverage above.

## Summary, unflattering parts included

* SARIMAX **does** beat last-value-carried-forward on most types, but the baseline is weak on trending data.
* It beats the seasonal-naive baseline on MAE for 7 of 8 types, not all.
* Intervals over-cover (99.6% vs 95% nominal).
* One 30-day hold-out per type is a single test, not a distribution; no rolling-origin evaluation was run here.
* Everything above concerns generated data. Nothing here measures how the model performs on real blood supply.
