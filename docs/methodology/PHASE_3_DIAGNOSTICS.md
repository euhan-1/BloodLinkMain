# Phase 3: Diagnostic checking

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the last 30 days) — what the 72-candidate grid search (d in {0,1} searched) and its AIC/Ljung-Box criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither stands in for the other.

## What the live system does versus what this report does

**The live order-selection grid search already runs Ljung-Box** — that is its selection criterion (SELECTION FIT
results below ARE what the grid search itself computed, not a re-derivation). **It runs no Jarque-Bera and no
heteroskedasticity test at all, ever**, on either fit. A live forecast request performs zero residual diagnostics:
after fitting the stored (or fallback) order it checks only that the optimiser reports convergence and the forecast
is finite. This report adds the full suite — Ljung-Box, Jarque-Bera, heteroskedasticity, residual ACF — to BOTH fits,
offline.

## Method (code: `run_methodology.py`)

d, p, q, seasonal P, seasonal Q all per type (PHASE_1/2; D=0, s=7 always). Residuals are the statsmodels **standardised
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
| A- | (2,0,0)x(1,0,1,7) | K=4 | 0.8080 / 0.1718 / 0.3691 / 0.1359 | 0.2897 / 0.0425 / 0.1649 / 0.0517 | 2.5 / 0.2907 / 0.06 / 2.62 | 1.12 / 0.5110 | 3/28 [9, 14, 23] | **FAIL** (Ljung-Box (corrected)) |
| AB+ | (1,0,2)x(1,0,1,7) | K=5 | 0.4994 / 0.1250 / 0.1919 / 0.3329 | 0.0418 / 0.0169 / 0.0488 / 0.1316 | 3.5 / 0.1727 / 0.23 / 3.09 | 1.10 / 0.5870 | 2/28 [6, 9] | **FAIL** (Ljung-Box (corrected)) |
| AB- | (1,0,1)x(1,0,1,7) | K=4 | 0.7213 / 0.6488 / 0.6107 / 0.7908 | 0.2127 / 0.3221 / 0.3519 / 0.5917 | 0.6 / 0.7412 / -0.09 / 2.92 | 1.14 / 0.4598 | 0/28 [] | **PASS** |
| B+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9708 / 0.5047 / 0.5312 / 0.7104 | 0.4099 / 0.1504 / 0.2274 / 0.4350 | 0.8 / 0.6747 / -0.11 / 2.97 | 1.06 / 0.7460 | 1/28 [8] | **PASS** |
| B- | (1,0,1)x(1,0,1,7) | K=4 | 0.5505 / 0.8258 / 0.6107 / 0.7433 | 0.1162 / 0.5243 / 0.3519 / 0.5320 | 0.5 / 0.7963 / 0.03 / 3.16 | 0.91 / 0.6156 | 2/28 [4, 20] | **PASS** |
| O+ | (1,0,2)x(1,0,1,7) | K=5 | 0.9775 / 0.9469 / 0.9473 / 0.9462 | 0.4426 / 0.6720 / 0.7643 / 0.8035 | 1.1 / 0.5718 / 0.12 / 3.10 | 1.22 / 0.2561 | 0/28 [] | **PASS** |
| O- | (0,0,2)x(1,0,1,7) | K=4 | 0.4176 / 0.3608 / 0.6407 / 0.5921 | 0.0685 / 0.1230 / 0.3807 / 0.3710 | 1.2 / 0.5384 / 0.09 / 2.78 | 0.94 / 0.7175 | 1/28 [4] | **PASS** |

* **PASS (6): A+, AB-, B+, B-, O+, O-.**
* **FAIL (2): A-, AB+.**

## Results — PRODUCTION FIT (full 420-day series; what a live forecast request actually runs)

| Type | Order | K | LB p uncorrected (7/14/21/28) | LB p corrected (7/14/21/28) | Jarque-Bera | Het. (breakvar) stat / p | Resid ACF outside 95% bound | Verdict |
|---|---|---|---|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | K=4 | 0.7786 / 0.2388 / 0.5103 / 0.4810 | 0.2604 / 0.0673 / 0.2655 / 0.2733 | 1.5 / 0.4762 / 0.04 / 2.71 | 0.79 / 0.1645 | 2/28 [9, 23] | **PASS** |
| A- | (2,0,0)x(1,0,1,7) | K=4 | 0.7838 / 0.1382 / 0.2393 / 0.0500 | 0.2653 / 0.0317 / 0.0907 / 0.0153 | 2.5 / 0.2920 / 0.01 / 2.62 | 1.15 / 0.4282 | 3/28 [9, 14, 23] | **FAIL** (Ljung-Box (corrected)) |
| AB+ | (1,0,2)x(1,0,1,7) | K=5 | 0.5490 / 0.0881 / 0.1841 / 0.3129 | 0.0518 / 0.0104 / 0.0460 / 0.1205 | 3.1 / 0.2121 / 0.21 / 3.11 | 1.11 / 0.5282 | 2/28 [9, 10] | **FAIL** (Ljung-Box (corrected)) |
| AB- | (1,0,1)x(1,0,1,7) | K=4 | 0.8224 / 0.8509 / 0.8131 / 0.9112 | 0.3055 / 0.5626 / 0.5815 / 0.7751 | 0.7 / 0.7206 / -0.08 / 2.90 | 1.16 / 0.3907 | 0/28 [] | **PASS** |
| B+ | (1,1,2)x(1,0,1,7) | K=5 | 0.9585 / 0.4497 / 0.4481 / 0.6050 | 0.3635 / 0.1223 / 0.1718 / 0.3291 | 0.6 / 0.7404 / -0.09 / 2.96 | 1.05 / 0.7826 | 1/28 [8] | **PASS** |
| B- | (1,0,1)x(1,0,1,7) | K=4 | 0.6630 / 0.9242 / 0.6854 / 0.8736 | 0.1736 / 0.7006 / 0.4264 / 0.7113 | 0.1 / 0.9661 / 0.01 / 3.06 | 0.91 / 0.5652 | 2/28 [4, 20] | **PASS** |
| O+ | (1,0,2)x(1,0,1,7) | K=5 | 0.9836 / 0.8987 / 0.9690 / 0.9471 | 0.4814 / 0.5529 / 0.8300 / 0.8056 | 1.5 / 0.4746 / 0.15 / 3.04 | 1.24 / 0.2083 | 0/28 [] | **PASS** |
| O- | (0,0,2)x(1,0,1,7) | K=4 | 0.5956 / 0.4525 / 0.6558 / 0.5873 | 0.1369 / 0.1747 / 0.3956 / 0.3664 | 1.2 / 0.5384 / 0.06 / 2.75 | 1.05 / 0.7733 | 0/28 [] | **PASS** |

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
| A- | 0.2921 | 0.1255 | 0.1823 | 4.32 | 0.84 | 7.87 | 1.56 | beats baseline | **PASS** |
| AB+ | 0.8426 | 0.9525 | 0.7926 | 4.19 | 0.96 | 4.35 | 1.17 | beats baseline | **PASS** |
| AB- | 7.4e-05 | 4.3e-06 | 5.0e-08 | 7.50 | 0.67 | 7.18 | 0.84 | loses to baseline | **FAIL** |
| B+ | 0.3722 | 0.3024 | 0.4704 | 4.02 | 2.69 | 5.31 | 3.31 | beats baseline | **PASS** |
| B- | 0.0033 | 0.0030 | 0.0072 | 6.79 | 0.82 | 9.71 | 1.33 | beats baseline | **FAIL** |
| O+ | 0.7786 | 0.5979 | 0.2328 | 5.35 | 6.23 | 17.32 | 15.86 | beats baseline | **PASS** |
| O- | 0.8065 | 0.8532 | 0.9315 | 5.71 | 1.48 | 7.32 | 1.92 | beats baseline | **PASS** |

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
* **AB- and B- are the two smallest series here (means of roughly 7.7 and 11.4 units, against 67-87 for A+ and O+),
  and they are the only two types that still fail the one-step hold-out check even restricted to lag 7 alone — the
  single lag with the least small-sample concern (checked separately from the lags 14/21/28 shown above; not a
  re-reading of the same result). That is not a coincidence to explain away: a Gaussian ARMA is describing a series
  where the actual outcomes are small non-negative integers, often under 10, and the model has no way to know that.
  This is a limitation of the model family for these two types, not an unexplained failure of the fitted order — a
  count model (e.g. an integer-valued or Poisson/negative-binomial time series model) would be a more appropriate
  description of a series this small, and might well resolve what looks here like a serial-correlation problem.
