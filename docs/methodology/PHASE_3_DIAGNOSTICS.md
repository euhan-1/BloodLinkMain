# Phase 3: Diagnostic checking

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the last 30 days) — what the 36-candidate grid search and its AIC/Ljung-Box criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither stands in for the other.

## What the live system does versus what this report does

**The live order-selection grid search already runs Ljung-Box** — that is its selection criterion (SELECTION FIT
results below ARE what the grid search itself computed, not a re-derivation). **It runs no Jarque-Bera and no
heteroskedasticity test at all, ever**, on either fit. A live forecast request performs zero residual diagnostics:
after fitting the stored (or fallback) order it checks only that the optimiser reports convergence and the forecast
is finite. This report adds the full suite — Ljung-Box, Jarque-Bera, heteroskedasticity, residual ACF — to BOTH fits,
offline.

## Method (code: `run_methodology.py`)

d=1, D=0, s=7 always; p, q, seasonal P, seasonal Q per type (PHASE_1/2). Residuals are the statsmodels **standardised
one-step-ahead in-sample forecast errors** of the relevant fit. The first `burn` observations (likelihood burn-in) are
dropped.

* **Ljung-Box** at lags 7, 14, 21 and 28. *Uncorrected* is the statsmodels default (`model_df=0`); *corrected* uses
  degrees of freedom = lag - K, where K = p+q+seasonal_p+seasonal_q (per type). At lag 7 the corrected test can have
  very few degrees of freedom, where the chi-square approximation is unreliable, so lag 7 is shown but **the verdict
  uses corrected p at lags 14, 21 and 28 only** — the same lags the selection criterion itself requires.
* **Jarque-Bera** (normality) and **heteroskedasticity** (`breakvar`, last third vs first third of the sample).
* **Residual ACF** to 28 lags against the 95% bound; about 1.4 of 28 lags are expected outside it by chance.
* **Hold-out Ljung-Box, one-step-ahead ONLY.** Ljung-Box on the STATIC (fixed-origin, multi-step) 30-day forecast
  errors PHASE_4's MAPE/RMSE use would be INVALID: h-step-ahead errors from one fixed origin share the same
  underlying innovations propagated forward through the ARMA structure and are autocorrelated BY CONSTRUCTION even
  for a correctly specified model, and n=30 could not support lags 14/21/28 regardless. Instead,
  `f_sel.append(holdout, refit=False)` extends the SELECTION FIT through the hold-out WITHOUT re-estimating
  parameters, giving 30 genuine one-step-ahead standardized residuals — exactly what Ljung-Box needs. Uncorrected:
  nothing was estimated on this data.

**Verdict rule (applied identically to both fits): PASS only if corrected Ljung-Box p > 0.05 at lags 14, 21 and 28,
Jarque-Bera p > 0.05, AND heteroskedasticity p > 0.05.**
**Hold-out verdict rule: PASS only if the one-step-ahead (uncorrected) Ljung-Box p > 0.05 at all of lags 14, 21 and 28.**

## Results — SELECTION FIT (training window, ending 2026-08-27; what the criterion was judged on)

| Type | Order | K | LB p uncorrected (7/14/21/28) | LB p corrected (7/14/21/28) | Jarque-Bera | Het. (breakvar) stat / p | Resid ACF outside 95% bound | Verdict |
|---|---|---|---|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | K=4 | 0.8739 / 0.2479 / 0.4991 / 0.4888 | 0.3738 / 0.0710 / 0.2566 / 0.2797 | 1.8 / 0.3992 / -0.01 / 2.66 | 0.89 / 0.4998 | 1/28 [9] | **PASS** |
| A- | (1,1,2)x(1,0,1,7) | K=5 | 0.7604 / 0.1941 / 0.3851 / 0.1227 | 0.1245 / 0.0320 / 0.1352 / 0.0339 | 3.1 / 0.2105 / 0.06 / 2.57 | 1.13 / 0.5016 | 2/28 [9, 23] | **FAIL** (Ljung-Box (corrected)) |
| AB+ | (1,1,2)x(1,0,1,7) | K=5 | 0.4916 / 0.1104 / 0.1973 / 0.3424 | 0.0403 / 0.0142 / 0.0507 / 0.1371 | 3.3 / 0.1963 / 0.22 / 3.13 | 1.11 / 0.5562 | 2/28 [6, 9] | **FAIL** (Ljung-Box (corrected)) |
| AB- | (1,1,1)x(1,0,1,7) | K=4 | 0.5933 / 0.5647 / 0.4191 / 0.6179 | 0.1357 / 0.2518 / 0.1980 / 0.3960 | 0.1 / 0.9291 / -0.04 / 2.94 | 1.21 / 0.2936 | 1/28 [17] | **PASS** |
| B+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9708 / 0.5047 / 0.5312 / 0.7104 | 0.4099 / 0.1504 / 0.2274 / 0.4350 | 0.8 / 0.6747 / -0.11 / 2.97 | 1.06 / 0.7460 | 1/28 [8] | **PASS** |
| B- | (1,1,2)x(1,0,1,7) | K=5 | 0.5378 / 0.8224 / 0.5604 / 0.6449 | 0.0494 / 0.4250 / 0.2492 / 0.3667 | 0.1 / 0.9552 / 0.02 / 3.06 | 0.91 / 0.5974 | 2/28 [4, 20] | **PASS** |
| O+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9324 / 0.9027 / 0.9152 / 0.9353 | 0.2969 / 0.5611 / 0.6867 / 0.7777 | 1.1 / 0.5643 / 0.11 / 3.15 | 1.21 / 0.2838 | 0/28 [] | **PASS** |
| O- | (1,1,2)x(1,0,1,7) | K=5 | 0.3338 / 0.3783 / 0.6957 / 0.6076 | 0.0184 / 0.0910 / 0.3694 / 0.3315 | 1.6 / 0.4472 / 0.05 / 2.69 | 0.97 / 0.8422 | 1/28 [4] | **PASS** |

* **PASS (6): A+, AB-, B+, B-, O+, O-.**
* **FAIL (2): A-, AB+.**

## Results — PRODUCTION FIT (full 420-day series; what a live forecast request actually runs)

| Type | Order | K | LB p uncorrected (7/14/21/28) | LB p corrected (7/14/21/28) | Jarque-Bera | Het. (breakvar) stat / p | Resid ACF outside 95% bound | Verdict |
|---|---|---|---|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | K=4 | 0.7786 / 0.2388 / 0.5103 / 0.4810 | 0.2604 / 0.0673 / 0.2655 / 0.2733 | 1.5 / 0.4762 / 0.04 / 2.71 | 0.79 / 0.1645 | 2/28 [9, 23] | **PASS** |
| A- | (1,1,2)x(1,0,1,7) | K=5 | 0.7350 / 0.1412 / 0.2214 / 0.0359 | 0.1119 / 0.0201 / 0.0596 / 0.0072 | 3.1 / 0.2163 / 0.00 / 2.58 | 1.15 / 0.4076 | 2/28 [9, 23] | **FAIL** (Ljung-Box (corrected)) |
| AB+ | (1,1,2)x(1,0,1,7) | K=5 | 0.5761 / 0.0800 / 0.1870 / 0.3163 | 0.0581 / 0.0091 / 0.0470 / 0.1224 | 3.0 / 0.2246 / 0.20 / 3.14 | 1.13 / 0.4879 | 3/28 [6, 9, 10] | **FAIL** (Ljung-Box (corrected)) |
| AB- | (1,1,1)x(1,0,1,7) | K=4 | 0.5393 / 0.5875 / 0.4896 / 0.6819 | 0.1114 / 0.2697 / 0.2493 / 0.4620 | 0.2 / 0.8991 / -0.04 / 2.93 | 1.25 / 0.1973 | 2/28 [11, 17] | **PASS** |
| B+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9585 / 0.4497 / 0.4481 / 0.6050 | 0.3635 / 0.1223 / 0.1718 / 0.3291 | 0.6 / 0.7404 / -0.09 / 2.96 | 1.05 / 0.7827 | 1/28 [8] | **PASS** |
| B- | (1,1,2)x(1,0,1,7) | K=5 | 0.6340 / 0.9110 / 0.6218 / 0.7998 | 0.0738 / 0.5792 / 0.2994 / 0.5450 | 0.0 / 0.9959 / 0.00 / 2.98 | 0.90 / 0.5369 | 2/28 [4, 20] | **PASS** |
| O+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9909 / 0.9137 / 0.9723 / 0.9622 | 0.5490 / 0.5851 / 0.8414 / 0.8451 | 1.2 / 0.5493 / 0.13 / 3.07 | 1.19 / 0.3172 | 0/28 [] | **PASS** |
| O- | (1,1,2)x(1,0,1,7) | K=5 | 0.4380 / 0.3855 / 0.6651 / 0.5881 | 0.0315 / 0.0939 / 0.3390 / 0.3140 | 1.7 / 0.4286 / 0.03 / 2.69 | 1.08 / 0.6662 | 1/28 [8] | **PASS** |

* **PASS (6): A+, AB-, B+, B-, O+, O-.**
* **FAIL (2): A-, AB+.**

The order is identical in both fits (PHASE_2) — only the data changes. Where the two verdicts differ, that difference
is caused entirely by fitting on 30 more (or fewer) days: no type differs between the two fits.

## Results — hold-out (one-step-ahead, 30 days the SELECTION FIT never saw)

MAPE/RMSE are the SELECTION FIT's own STATIC 30-day-ahead forecast error over the hold-out —
valid out-of-sample accuracy evidence, distinct from (and not tested for independence the same way as) the one-step
LB columns. Baseline is a naive last-training-value-repeated forecast over the identical window.

| Type | LB p (one-step), lag 14 | lag 21 | lag 28 | MAPE % | RMSE | Baseline MAPE % | Baseline RMSE | vs baseline | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| A+ | 0.2784 | 0.3214 | 0.1573 | 4.53 | 3.25 | 6.07 | 5.28 | beats baseline | **PASS** |
| A- | 0.2644 | 0.1003 | 0.1691 | 4.29 | 0.84 | 7.87 | 1.56 | beats baseline | **PASS** |
| AB+ | 0.7656 | 0.9073 | 0.6863 | 4.09 | 0.93 | 4.35 | 1.17 | beats baseline | **PASS** |
| AB- | 2.6e-05 | 6.9e-07 | 8.3e-09 | 7.78 | 0.68 | 7.18 | 0.84 | loses to baseline | **FAIL** |
| B+ | 0.3722 | 0.3024 | 0.4704 | 4.02 | 2.69 | 5.31 | 3.31 | beats baseline | **PASS** |
| B- | 0.0030 | 0.0030 | 0.0063 | 6.82 | 0.82 | 9.71 | 1.33 | beats baseline | **FAIL** |
| O+ | 0.8478 | 0.7865 | 0.2733 | 5.24 | 6.21 | 17.32 | 15.86 | beats baseline | **PASS** |
| O- | 0.7518 | 0.7698 | 0.8543 | 5.73 | 1.49 | 7.32 | 1.92 | beats baseline | **PASS** |

**Hold-out PASS (6): A+, A-, AB+, B+, O+, O-.**

## All three, compared

* **Pass PRODUCTION FIT and hold-out (4): A+, B+, O+, O-.** The types where both "the
  order fits the full history well" and "its forecast errors on genuinely new data are independent" hold.
* **Pass PRODUCTION FIT, FAIL hold-out (2): AB-, B-.** Fits the full history but
  its forecast errors on new data are still serially correlated.
* **FAIL PRODUCTION FIT, pass hold-out (2): A-, AB+.** A
  reminder that in-sample and out-of-sample diagnostics answer different questions.

## Caveats

* No test here adjusts for multiple comparisons across 8 types, or across the two fits.
* The hold-out is 30 days, once, per type — a single test, not a distribution; no
  rolling-origin evaluation was run.
* Jarque-Bera and heteroskedasticity are evaluated in-sample only (on both fits); the selection criterion and the
  hold-out check are both Ljung-Box only (see main.py's ORDER SELECTION module comment for why).
