# Phase 1: Identification

> **Data notice.** The 180-day series analysed here (`northside_180d_history.csv`, 2026-03-30 to 2026-09-25, 8 blood types, 1,440 daily counts) is **generated demonstration data**, not real blood bank records. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

## What the live system does versus what this report does

**The live system performs no identification.** `server/main.py` contains no ADF test, no ACF/PACF computation and no
order search (verified by grep: no `adfuller`, `acf`, `pacf`, `acorr_ljungbox` or AIC-based selection anywhere in the
file). It applies one fixed order to every facility and blood type: `FACILITY_SARIMAX_ORDER = (0, 1, 4)` and
`FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)` (`main.py:68-69`), fitted by `_fit_sarimax_facility_forecast`
(`main.py:735`). Its only per-series checks are that the optimiser reports convergence and that the forecast is finite.

That order was chosen **offline during development**, on synthetic data, before any facility existed
(`server/SYNTHETIC_SARIMAX_VALIDATION.md`). An attempt to make the choice per-series with a learned selector was tried
twice and not adopted (`server/sarimax_selector_common.py`, `train_sarimax_selector.py`).

**This report runs identification to VALIDATE the fixed order against this series, not to CHOOSE one.** Nothing below
changes what the system does. Where the readings disagree with the fixed order, that is reported as a disagreement.

## Method (code: `run_methodology.py`, phase 1 block)

* ADF test, `statsmodels.tsa.stattools.adfuller`, constant-only regression, lag length chosen by AIC, alpha = 0.05.
* Differencing: difference repeatedly until ADF rejects the unit root (max 2). The live model always uses d = 1, so ADF is
  also reported on the first difference regardless of what the minimum needed was.
* ACF and PACF of the **first-differenced** series to 28 lags (four weekly cycles); PACF by the Yule-Walker method.
  95% bound = 1.96/sqrt(n) = 0.146 (n = 179 differences).
* "Suggested q / p" below is the crude textbook rule: the number of consecutive significant lags starting at lag 1.

## 1. Stationarity

| Type | ADF stat (raw) | p (raw) | Verdict (raw) | Min d to pass | ADF stat (d=1) | p (d=1) | Verdict (d=1) |
|---|---|---|---|---|---|---|---|
| A+ | 0.51 | 0.9853 | non-stationary | 1 | -5.38 | 3.8e-06 | stationary |
| A- | 0.22 | 0.9733 | non-stationary | 1 | -8.60 | 7.1e-14 | stationary |
| AB+ | 0.16 | 0.9698 | non-stationary | 1 | -11.61 | 2.5e-21 | stationary |
| AB- | -0.22 | 0.9363 | non-stationary | 1 | -4.45 | 0.0002 | stationary |
| B+ | 1.99 | 0.9987 | non-stationary | 1 | -6.69 | 4.2e-09 | stationary |
| B- | 0.07 | 0.9642 | non-stationary | 1 | -10.23 | 5.1e-18 | stationary |
| O+ | -0.13 | 0.9469 | non-stationary | 1 | -4.46 | 0.0002 | stationary |
| O- | -0.06 | 0.9531 | non-stationary | 1 | -6.53 | 1.0e-08 | stationary |

All 8 of 8 series are non-stationary in levels (p >= 0.93) and become stationary after exactly one difference
(p <= 3e-4 for every type). This supports **d = 1** in the fixed order. No series needed d = 2.

## 2. ACF / PACF of the differenced series

| Type | Significant ACF lags | Suggested q | Significant PACF lags | Suggested p | Seasonal-multiple ACF lags | Seasonal-multiple PACF lags |
|---|---|---|---|---|---|---|
| A+ | [1, 11] | 1 | [1, 2, 3, 4, 5, 10, 11, 12, 27] | 5 | [] | [] |
| A- | [1, 18, 19] | 1 | [1, 2, 3, 4, 5, 6] | 6 | [] | [] |
| AB+ | [1, 18, 19] | 1 | [1, 2, 3, 4, 5, 6, 18] | 6 | [] | [] |
| AB- | [1, 4, 14, 18] | 1 | [1, 2, 3, 4, 5, 6, 7, 13, 26] | 7 | [14] | [7] |
| B+ | [1, 2, 14, 17, 21] | 2 | [1, 2, 3, 4, 5, 6, 12] | 6 | [14, 21] | [] |
| B- | [1, 4, 7, 9] | 1 | [1, 2, 3, 4, 5, 6] | 6 | [7] | [] |
| O+ | [1, 2, 3, 4, 8, 15, 16] | 4 | [1, 2, 3, 4, 5, 6, 7, 11, 15, 27] | 7 | [] | [7] |
| O- | [1, 2, 11, 12, 14, 15, 16, 25, 27] | 2 | [1, 2, 3, 4, 5, 6, 12, 13, 28] | 6 | [14] | [28] |

## 3. Is weekly (s = 7) seasonality actually in the data?

The ACF/PACF above show weak evidence at lag 7 (see reading below), so weekly structure was tested directly: detrend each
series with a centred 7-day moving average, then one-way ANOVA on the residual by day of week
(`run_extra_checks.py`).

| Type | F | p | Weekday range (units) | Series mean (units) |
|---|---|---|---|---|
| A+ | 9.21 | 1.0e-08 | 5.11 | 83.4 |
| A- | 8.10 | 1.1e-07 | 1.28 | 19.9 |
| AB+ | 12.59 | 1.1e-11 | 1.9 | 27.1 |
| AB- | 8.72 | 2.9e-08 | 0.68 | 9.6 |
| B+ | 9.82 | 2.9e-09 | 6.68 | 93.9 |
| B- | 7.33 | 5.7e-07 | 1.52 | 22.7 |
| O+ | 10.62 | 5.6e-10 | 12.35 | 130.7 |
| O- | 5.68 | 2.1e-05 | 2.32 | 29.2 |

A weekday effect is present in all 8 types (p < 1e-4), largest in absolute terms for O+ (12.35 units
peak-to-trough on a mean of 130.7). Weekly seasonality genuinely exists in this series.

## Reading against the fixed order (0,1,4)x(1,0,1,7)

* **d = 1: supported** for all 8 types (section 1).
* **q = 4: only weakly supported.** The leading run of significant ACF lags is >= 4 for 1 type(s)
  (O+) and <= 2 for 7 (A+, A-, AB+, AB-, B+, B-, O-). For most types the ACF cuts off at lag 1 or 2, which
  would suggest q = 1 or 2, not 4. Phase 2 agrees: the MA(2)-MA(4) coefficients are mostly not significant.
* **p = 0: consistent with an MA-dominated process.** The PACF stays significant out to lag 5-7 for every type instead of
  cutting off, so a pure AR model is not suggested. This is the signature of an MA process.
* **Seasonal (1,0,1,7): weakly supported by ACF/PACF, supported by the ANOVA.** After differencing, lag 7 is significant in
  the ACF for only 1 type(s) (B-) and in the PACF for 2 (AB-, O+). The
  ACF/PACF alone would not justify seasonal terms; the weekday ANOVA does show the seasonality exists, and Phase 2 shows the
  seasonal terms cut AIC by tens of points. No seasonal differencing (D = 0) is used, and nothing here tests that choice.

## Limitations

* Identification by eye-balled ACF/PACF is subjective. The "suggested q/p" rule is the simplest possible reading.
* 180 days is roughly 26 weekly cycles; lags beyond 28 were not examined.
* One series per blood type, from a generated source. Nothing here says the order would validate on a real facility.
