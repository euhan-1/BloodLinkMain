# Phase 1: Identification

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

## What the live system does versus what this report does

**The live system selects a SARIMAX order per facility per blood type, offline** — never inside a forecast request.
`server/select_orders.py` (the primary path) and, bounded, a background task after each historical upload, both call
`main.py:_run_order_selection_for_facility`, which fits a 36-candidate grid (p,q in {0,1,2}, seasonal P,Q in {0,1},
d=1, D=0, s=7 fixed) on the series minus the last 30 days, and picks the lowest-AIC candidate
whose Ljung-Box test passes on its own training residuals — see PHASE_2 and PHASE_3. A type with nothing selected
falls back to the fixed `FACILITY_SARIMAX_ORDER = (0, 1, 4)` / `FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)`
(`main.py:97-98`).

**Still, no ADF test, no ACF/PACF computation ever runs inside the grid search or a request** (verified by grep: no
`adfuller`, `acf`, or `pacf` anywhere in `main.py`). Order selection ranks candidates by AIC and Ljung-Box alone; it
never looks at a differencing test or a correlogram. **This report runs identification to check the selected orders
against what ADF/ACF/PACF would suggest, not to choose them.** Where the readings disagree with what was selected,
that is reported as a disagreement, not corrected. The orders shown below are each type's SELECTION FIT order — see
PHASE_2/3 for what that means precisely.

## Method (code: `run_methodology.py`, phase 1 block)

* ADF test, `statsmodels.tsa.stattools.adfuller`, constant-only regression, lag length chosen by AIC, alpha = 0.05.
* Differencing: difference repeatedly until ADF rejects the unit root (max 2). Every selected and fallback order alike
  uses d = 1, so ADF is also reported on the first difference regardless of what the minimum needed was.
* ACF and PACF of the **first-differenced** series to 28 lags; PACF by the Yule-Walker method.
  95% bound = 1.96/sqrt(n) = 0.096 (n = 419 differences).
* "Suggested q / p" below is the crude textbook rule: the number of consecutive significant lags starting at lag 1.

## 1. Stationarity

| Type | ADF stat (raw) | p (raw) | Verdict (raw) | Min d to pass | ADF stat (d=1) | p (d=1) | Verdict (d=1) |
|---|---|---|---|---|---|---|---|
| A+ | -1.62 | 0.4741 | non-stationary | 1 | -7.91 | 4.0e-12 | stationary |
| A- | -1.86 | 0.3534 | non-stationary | 1 | -7.35 | 1.0e-10 | stationary |
| AB+ | -2.75 | 0.0664 | non-stationary | 1 | -6.61 | 6.3e-09 | stationary |
| AB- | -3.43 | 0.0100 | stationary | 0 | -6.78 | 2.5e-09 | stationary |
| B+ | -2.54 | 0.1061 | non-stationary | 1 | -9.02 | 6.0e-15 | stationary |
| B- | -2.55 | 0.1038 | non-stationary | 1 | -8.84 | 1.7e-14 | stationary |
| O+ | -2.03 | 0.2721 | non-stationary | 1 | -6.66 | 4.8e-09 | stationary |
| O- | -1.43 | 0.5656 | non-stationary | 1 | -6.32 | 3.1e-08 | stationary |

7 of 8 series are non-stationary in levels (raw p up to 0.566) and every one of the 8 becomes
stationary after one difference (d1 p at most 3.1e-08). **AB-** already rejects the unit root in levels (min d = 0) — the selected order still uses d = 1 there regardless, since d is fixed across the whole grid. 
d = 1 is well supported for the other 7 types. No series needed d = 2.

## 2. ACF / PACF of the differenced series

| Type | Significant ACF lags | Suggested q | Significant PACF lags | Suggested p | Seasonal-multiple ACF lags | Seasonal-multiple PACF lags |
|---|---|---|---|---|---|---|
| A+ | [2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18, 20, 21, 22, 23, 24, 25, 27, 28] | 0 | [2, 3, 4, 5, 6, 8, 9, 11, 12, 13, 18, 19, 22, 25] | 0 | [7, 14, 21, 28] | [] |
| A- | [3, 4, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18, 20, 21, 22, 24, 25, 26, 27, 28] | 0 | [3, 4, 5, 8, 11, 12, 15, 18, 23] | 0 | [7, 14, 21, 28] | [] |
| AB+ | [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18, 20, 21, 22, 24, 25, 27, 28] | 0 | [2, 3, 4, 5, 8, 10, 11, 12, 13, 14, 15, 16] | 0 | [7, 14, 21, 28] | [14] |
| AB- | [1, 2, 4, 7, 11, 14, 15, 17, 18, 21, 22, 24, 25, 28] | 2 | [1, 2, 3, 4, 5, 6, 11, 12, 13] | 6 | [7, 14, 21, 28] | [] |
| B+ | [2, 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 17, 18, 20, 21, 22, 24, 25, 27, 28] | 0 | [2, 3, 4, 5, 11, 12, 13, 19] | 0 | [7, 14, 21, 28] | [] |
| B- | [2, 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 17, 18, 19, 20, 21, 22, 24, 25, 27, 28] | 0 | [2, 3, 4, 5, 6, 11, 12, 13, 19] | 0 | [7, 14, 21, 28] | [] |
| O+ | [2, 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 17, 18, 20, 21, 22, 24, 25, 27, 28] | 0 | [2, 3, 4, 5, 8, 10, 11, 17, 18, 21] | 0 | [7, 14, 21, 28] | [21] |
| O- | [1, 2, 3, 4, 6, 7, 8, 10, 11, 13, 14, 15, 17, 18, 20, 21, 22, 24, 25, 27, 28] | 4 | [1, 2, 3, 4, 5, 10, 11, 12, 13, 18] | 5 | [7, 14, 21, 28] | [] |

## 3. Is weekly (s = 7) seasonality actually in the data?

The ACF/PACF above show weak evidence at lag 7 for some types (see reading below), so weekly structure was tested
directly: detrend each series with a centred 7-day moving average, then one-way ANOVA on the residual by day of week
(`run_extra_checks.py`).

| Type | F | p | Weekday range (units) | Series mean (units) |
|---|---|---|---|---|
| A+ | 88.26 | 1.5e-70 | 8.4 | 67.0 |
| A- | 72.12 | 5.5e-61 | 1.89 | 16.9 |
| AB+ | 73.37 | 9.1e-62 | 2.27 | 19.5 |
| AB- | 32.10 | 1.3e-31 | 0.79 | 7.7 |
| B+ | 80.16 | 7.1e-66 | 6.63 | 52.4 |
| B- | 68.47 | 1.1e-58 | 1.39 | 11.4 |
| O+ | 84.48 | 2.1e-68 | 10.84 | 87.0 |
| O- | 83.43 | 8.6e-68 | 3.4 | 25.0 |

A weekday effect is present in all 8 types (p < 1e-4), largest in absolute terms for O+
(10.84 units peak-to-trough). Weekly seasonality genuinely exists in this series.

## Reading against Northside's SELECTION FIT orders

| Type | Selected order | ACF-suggested q | PACF stays significant to | q match? |
|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | 0 | 25 | no |
| A- | (1,1,2)x(1,0,1,7) | 0 | 23 | no |
| AB+ | (1,1,2)x(1,0,1,7) | 0 | 16 | no |
| AB- | (1,1,1)x(1,0,1,7) | 2 | 13 | no |
| B+ | (1,1,2)x(1,0,1,7) | 0 | 19 | no |
| B- | (1,1,2)x(1,0,1,7) | 0 | 19 | no |
| O+ | (1,1,2)x(1,0,1,7) | 0 | 21 | no |
| O- | (1,1,2)x(1,0,1,7) | 4 | 18 | no |

* **d = 1: used by all 8** selected orders; AB- is where identification alone would have
  allowed d = 0 and the grid never tested it.
* **q: matches the ACF's leading run for 0 of 8 types.** Where they disagree the selected q and the
  ACF-suggested q are usually close (within 1-2); AIC and the training-window Ljung-Box are picking up structure a
  single leading-run reading doesn't capture, in both directions.
* **p: consistent with an MA-dominated process throughout.** The PACF stays significant well past lag 5 for every
  type instead of cutting off, so a pure AR model is never suggested — every selected order uses p <= 2, seasonal P = 1.
* **Seasonal (P=1, Q=1, s=7): used by every selected order, and supported by the ACF for 8 of 8 types**
  (A+, A-, AB+, AB-, B+, B-, O+, O-); the PACF is significant at lag 7 for 0 (none). The weekday
  ANOVA independently confirms the seasonality exists. No seasonal differencing (D = 0) is used, and nothing here
  tests that choice.

## Limitations

* Identification by eye-balled ACF/PACF is subjective. The "suggested q/p" rule is the simplest possible reading.
* 420 days is roughly 60 weekly cycles; lags beyond 28 were not examined.
* One series per blood type, from a generated source. Nothing here says a selected order would validate on a real facility.
