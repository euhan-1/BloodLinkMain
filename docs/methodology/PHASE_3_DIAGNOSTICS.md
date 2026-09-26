# Phase 3: Diagnostic checking

> **Data notice.** The 180-day series analysed here (`northside_180d_history.csv`, 2026-03-30 to 2026-09-25, 8 blood types, 1,440 daily counts) is **generated demonstration data**, not real blood bank records. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

## What the live system does versus what this report does

**The live system performs no residual diagnostics.** After fitting the fixed order it checks only that the optimiser
reports convergence and that the forecast values are finite (same function); it does not run Ljung-Box, a normality
test, a heteroskedasticity test or any residual check, and a fit whose residuals fail every test below is served exactly the
same as one that passes. These diagnostics were run offline to test the fixed order on this series. They are not part of
request handling.

## Method (code: `run_methodology.py`, phase 2/3 block)

Residuals are the statsmodels **standardised one-step-ahead forecast errors** of the full-sample fixed-order fit
(`SARIMAX(0,1,4)x(1,0,1,7)` + binary dengue flag). The first 13 observations (likelihood burn-in for the
differenced, diffuse-initialised state) are dropped, leaving n = 167.

* **Ljung-Box** at lags 7, 14, 21 and 28 (one to four full weekly cycles). Two p-values are shown. *Uncorrected* is the
  statsmodels default (`model_df = 0`) and is too generous, because it ignores that 6 ARMA parameters were fitted.
  *Corrected* uses degrees of freedom = lag - 6. At lag 7 the corrected test has only 1 degree of freedom, where the
  chi-square approximation is unreliable, so lag 7 is shown but **the verdict uses corrected p at lags 14, 21 and 28 only.**
* **Jarque-Bera** (normality), via `fit.test_normality('jarquebera')`.
* **Heteroskedasticity:** `fit.test_heteroskedasticity('breakvar')`, which compares residual variance in the last third of the
  sample with the first third.
* **Residual ACF** to 28 lags against the 95% bound +/-1.96/sqrt(n) = +/-0.152. At 5%, about 1.4 of 28 lags
  are expected outside the bound by chance; up to 2 is treated as chance.

**Verdict rule (stated before looking at results): PASS only if all three hold: corrected Ljung-Box p > 0.05 at lags 14, 21 and
28; Jarque-Bera p > 0.05; heteroskedasticity p > 0.05.** Failing any one is a FAIL.

## Results

Ljung-Box p at lags 7 / 14 / 21 / 28. JB column: statistic / p / skewness / kurtosis (normal = 0 and 3).

| Type | LB p uncorrected | LB p corrected (df=lag-6) | Jarque-Bera | Het. (breakvar) stat / p | Resid ACF outside 95% bound | Verdict |
|---|---|---|---|---|---|---|
| A+ | 0.6576 / 0.5287 / 0.3836 / 0.6237 | 0.0251 / 0.1128 / 0.1007 / 0.2934 | 0.0 / 0.9787 / 0.00 / 3.08 | 0.53 / 0.0191 | 0/28 [] | **FAIL** (heteroskedasticity) |
| A- | 0.8766 / 0.5306 / 0.4296 / 0.2962 | 0.0788 / 0.1137 / 0.1220 / 0.0867 | 0.5 / 0.7670 / 0.04 / 2.74 | 0.71 / 0.1945 | 2/28 [19, 22] | **PASS** |
| AB+ | 0.9646 / 0.9977 / 0.9633 / 0.9822 | 0.1668 / 0.8970 / 0.7544 / 0.8783 | 2.8 / 0.2418 / -0.09 / 3.61 | 0.45 / 0.0031 | 0/28 [] | **FAIL** (heteroskedasticity) |
| AB- | 0.6685 / 0.7205 / 0.3963 / 0.4159 | 0.0264 / 0.2282 / 0.1064 / 0.1468 | 2.3 / 0.3214 / -0.11 / 2.48 | 0.61 / 0.0700 | 1/28 [4] | **PASS** |
| B+ | 0.9586 / 0.7141 / 0.8307 / 0.3877 | 0.1549 / 0.2230 / 0.4627 / 0.1312 | 2.3 / 0.3094 / 0.24 / 2.67 | 0.64 / 0.1020 | 2/28 [13, 26] | **PASS** |
| B- | 0.5221 / 0.1893 / 0.3188 / 0.4805 | 0.0131 / 0.0184 / 0.0744 / 0.1859 | 0.6 / 0.7593 / -0.02 / 2.72 | 0.42 / 0.0015 | 1/28 [9] | **FAIL** (Ljung-Box (corrected), heteroskedasticity) |
| O+ | 0.7624 / 0.4504 / 0.1146 / 0.1334 | 0.0416 / 0.0820 / 0.0162 / 0.0277 | 14.4 / 0.0007 / -0.44 / 4.14 | 0.40 / 0.0009 | 2/28 [16, 27] | **FAIL** (Ljung-Box (corrected), Jarque-Bera, heteroskedasticity) |
| O- | 0.9934 / 0.9064 / 0.7671 / 0.7506 | 0.2985 / 0.4675 / 0.3790 / 0.4220 | 13.9 / 0.0010 / -0.52 / 3.95 | 0.42 / 0.0016 | 0/28 [] | **FAIL** (Jarque-Bera, heteroskedasticity) |

## Verdict

* **PASS (3): A-, AB-, B+.**
* **FAIL (5): A+ [heteroskedasticity], AB+ [heteroskedasticity], B- [Ljung-Box (corrected), heteroskedasticity], O+ [Ljung-Box (corrected), Jarque-Bera, heteroskedasticity], O- [Jarque-Bera, heteroskedasticity].**

**Serial correlation is the model's strong point; heteroskedasticity is its weak point.** Every type passes Ljung-Box on the
uncorrected default at every lag. With the degrees-of-freedom correction, B-, O+
fail at lag 14, 21 or 28, and A+, AB-, B-, O+ would also fail at lag 7. Residual ACF is within the bound for most lags
(no more than 2 of 28 outside for any type), so the mean structure is adequately captured.

The variance is not. The heteroskedasticity test rejects for 5 of 8 types. This is expected for this data: every
series falls steadily, so counts and their noise shrink, and a model with constant innovation variance cannot follow
that. It affects the width of prediction intervals (Phase 4). O+ and O- also reject normality (skew -0.44 and -0.52, kurtosis
above 3.9), so their Gaussian interval bounds are less trustworthy.

## Caveats

* A- and B- are labelled PASS/FAIL on residuals that come from fits whose parameters are poorly identified (Phase 2). A pass
  there says the residuals look acceptable, not that the coefficients are reliable.
* No test here adjusts for multiple comparison across 8 types.
* Residual diagnostics are on the in-sample fit; Phase 4 tests out-of-sample behaviour.
