# Phase 4: Forecasting and out-of-sample validation

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the last 30 days) — what the 36-candidate grid search and its AIC/Ljung-Box criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither stands in for the other.

## What the live system does versus what this report does

Live, `GET /forecast` reads each type's stored order (or the fixed fallback) and fits it on the FULL available
history — the PRODUCTION FIT pattern — caching 7 checkpoints (days 0, 5, 10, 15, 20, 25, 30) per facility, type and
day. **The live system has no accuracy monitoring**: it never compares a forecast with what later happened. This
report's hold-out (the SELECTION FIT, forecasting the 30 days it was never trained on) is an
offline test using each type's own selected order, not the live path's own request/response cycle.

## Method (code: `run_methodology.py`)

* Hold out the last 30 days (2026-08-27 is the training window's last day
  for every type). Fit EACH type's own SELECTION FIT order (PHASE_2) on the training window, forecast
  30 days, and score against the actual held-out values.
* Metrics: MAPE, RMSE, MAE on the point forecast; interval coverage = fraction of actuals inside the 95% interval.
* Baselines: naive (last training value repeated) and seasonal-naive (the last observed week repeated).
* **Live-parity check, kept as its own explicit step** (not folded into `select_orders.py`'s own stored numbers):
  for every type, the production function `_fit_sarimax_facility_forecast` is called directly with that type's
  selected order on the same training series, and its checkpoint forecasts are compared with this script's own fit.
  "The deployed function reproduces an independent fit" is a different claim from "the model is accurate," and the
  two are reported separately.

## 1. Accuracy against the naive baseline (all per type's own selected order)

Cells are MAPE % / RMSE / MAE (units). Lower is better.

| Type | SARIMAX (selected order) | Naive (last value) | Seasonal naive (7d) |
|---|---|---|---|
| A+ | 4.53 / 3.25 / 2.78 | 6.07 / 5.28 / 3.93 | 5.24 / 4.06 / 3.27 |
| A- | 4.29 / 0.84 / 0.67 | 7.87 / 1.56 / 1.30 | 5.11 / 1.14 / 0.83 |
| AB+ | 4.09 / 0.93 / 0.75 | 4.35 / 1.17 / 0.83 | 5.55 / 1.38 / 1.03 |
| AB- | 7.78 / 0.68 / 0.58 | 7.18 / 0.84 / 0.57 | 8.25 / 0.86 / 0.60 |
| B+ | 4.02 / 2.69 / 2.04 | 5.31 / 3.31 / 2.67 | 7.20 / 4.39 / 3.63 |
| B- | 6.82 / 0.82 / 0.74 | 9.71 / 1.33 / 1.10 | 8.74 / 1.22 / 0.97 |
| O+ | 5.24 / 6.21 / 4.54 | 17.32 / 15.86 / 14.67 | 7.21 / 7.39 / 6.00 |
| O- | 5.73 / 1.49 / 1.25 | 7.32 / 1.92 / 1.57 | 6.50 / 1.83 / 1.43 |

SARIMAX beats naive-last on MAE for 7 of 8 types, and beats seasonal-naive on MAE for 8 of 8.
It loses to naive-last on MAE for AB-.

## 2. Prediction-interval coverage

Nominal coverage is 95%.

| Type | Actuals inside 95% interval | Coverage |
|---|---|---|
| A+ | 30/30 | 100.0% |
| A- | 29/30 | 96.7% |
| AB+ | 28/30 | 93.3% |
| AB- | 26/30 | 86.7% |
| B+ | 29/30 | 96.7% |
| B- | 29/30 | 96.7% |
| O+ | 26/30 | 86.7% |
| O- | 29/30 | 96.7% |

Overall **226 of 240 (94.2%)**.

## 3. Interval width by horizon

| Type | Day 1 | Day 5 | Day 10 | Day 15 | Day 20 | Day 25 | Day 30 | Day 30 / Day 1 |
|---|---|---|---|---|---|---|---|---|
| A+ | 12.7 | 14.5 | 14.6 | 14.6 | 14.6 | 14.6 | 14.6 | 1.15x |
| A- | 3.2 | 3.6 | 3.6 | 3.6 | 3.6 | 3.6 | 3.6 | 1.13x |
| AB+ | 3.7 | 4.4 | 4.4 | 4.4 | 4.4 | 4.4 | 4.4 | 1.18x |
| AB- | 1.9 | 2.0 | 2.0 | 2.0 | 2.0 | 2.0 | 2.0 | 1.06x |
| B+ | 9.7 | 11.9 | 12.0 | 12.0 | 12.1 | 12.2 | 12.3 | 1.27x |
| B- | 2.4 | 2.9 | 2.9 | 2.9 | 2.9 | 2.9 | 2.9 | 1.20x |
| O+ | 16.6 | 19.8 | 19.9 | 19.9 | 19.9 | 19.9 | 19.9 | 1.20x |
| O- | 4.7 | 5.5 | 5.6 | 5.6 | 5.6 | 5.6 | 5.6 | 1.19x |

## 4. Live-parity check

| Type | Order used | Matches the live function's own fit |
|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | yes |
| A- | (1,1,2)x(1,0,1,7) | yes |
| AB+ | (1,1,2)x(1,0,1,7) | yes |
| AB- | (1,1,1)x(1,0,1,7) | yes |
| B+ | (1,1,2)x(1,0,1,7) | yes |
| B- | (1,1,2)x(1,0,1,7) | yes |
| O+ | (1,1,2)x(1,0,1,7) | yes |
| O- | (1,1,2)x(1,0,1,7) | yes |

**8 of 8 types match exactly.** This is a check that `_fit_sarimax_facility_forecast`, called directly with
each type's selected order, reproduces this script's own independent fit on identical input — it says nothing about
forecast accuracy on its own, which is section 1 above.

## Caveats

* One 30-day hold-out per type is a single test, not a distribution; no rolling-origin
  evaluation was run here.
* Coverage and MAPE/RMSE are computed from the SELECTION FIT (trained on 390 days), not
  the PRODUCTION FIT (trained on all 420 days) — the live system's actual day-to-day forecast comes
  from the production fit, which by construction cannot be evaluated out-of-sample on data already inside it.
* Everything above concerns generated data. Nothing here measures how the model performs on real blood supply.
