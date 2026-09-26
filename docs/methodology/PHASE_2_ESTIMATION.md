# Phase 2: Estimation

> **Data notice.** The 180-day series analysed here (`northside_180d_history.csv`, 2026-03-30 to 2026-09-25, 8 blood types, 1,440 daily counts) is **generated demonstration data**, not real blood bank records. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

## What the live system does versus what this report does

The live system fits **one fixed model** per facility and blood type: `SARIMAX(0,1,4)x(1,0,1,7)` with a dengue-season
exogenous regressor, `trend=None`, `enforce_stationarity=False`, `enforce_invertibility=False`, `maxiter=200`
(inside `_fit_sarimax_facility_forecast`, `main.py:735`). It does not compare candidate orders and never looks at AIC or BIC. This report reproduces that exact
specification (`run_methodology.py` copies it; a parity check in Phase 4 confirms the live function and this script give
identical forecasts) and then fits five alternatives, offline, to test whether the fixed choice is evidenced.

**The exogenous variable in the live system is the BINARY flag** `dengue_season_index` (1.0 for June-October, else 0.0,
`main.py:639`). A graded index was investigated and not adopted (`server/DENGUE_INDEX_DERIVATION.md`). This report uses
the live binary flag.

## 1. Fixed order: every coefficient

SARIMAX in statsmodels is a regression with SARIMA errors: `y_t = beta * dengue_t + eta_t`, with `eta_t` following the
SARIMA model. `sigma2` is the innovation variance.

### A+  (AIC 970.6, BIC 995.6, log-likelihood -477.3, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -1.792 | 2.425 | -0.74 | 0.4598 |
| ma.L1 | -0.900 | 0.081 | -11.11 | 1.1e-28 |
| ma.L2 | 0.102 | 0.110 | 0.93 | 0.3542 |
| ma.L3 | -0.028 | 0.108 | -0.26 | 0.7929 |
| ma.L4 | 0.039 | 0.069 | 0.57 | 0.5658 |
| ar.S.L7 | 0.957 | 0.025 | 37.95 | 0.0e+00 |
| ma.S.L7 | -1.051 | 0.128 | -8.22 | 2.1e-16 |
| sigma2 | 14.629 | 2.749 | 5.32 | 1.0e-07 |

### A-  (AIC 507.0, BIC 532.0, log-likelihood -245.5, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -0.029 | 0.388 | -0.08 | 0.9399 |
| ma.L1 | -0.879 | 0.070 | -12.61 | 1.8e-36 |
| ma.L2 | 0.018 | 0.100 | 0.18 | 0.8590 |
| ma.L3 | -0.089 | 0.107 | -0.83 | 0.4052 |
| ma.L4 | 0.091 | 0.078 | 1.18 | 0.2387 |
| ar.S.L7 | 0.987 | 0.028 | 35.37 | 4.8e-274 |
| ma.S.L7 | -1.000 | 260.676 | -0.00 | 0.9969 |
| sigma2 | 0.964 | 251.150 | 0.00 | 0.9969 |

### AB+  (AIC 583.5, BIC 608.4, log-likelihood -283.7, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | 0.116 | 0.996 | 0.12 | 0.9070 |
| ma.L1 | -0.804 | 0.080 | -10.10 | 5.6e-24 |
| ma.L2 | 0.005 | 0.107 | 0.05 | 0.9613 |
| ma.L3 | 0.052 | 0.103 | 0.51 | 0.6126 |
| ma.L4 | -0.051 | 0.083 | -0.62 | 0.5374 |
| ar.S.L7 | 0.960 | 0.021 | 46.21 | 0.0e+00 |
| ma.S.L7 | -0.946 | 0.133 | -7.13 | 1.0e-12 |
| sigma2 | 1.594 | 0.196 | 8.13 | 4.4e-16 |

### AB-  (AIC 273.7, BIC 298.6, log-likelihood -128.8, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -0.293 | 0.313 | -0.94 | 0.3497 |
| ma.L1 | -0.886 | 0.079 | -11.15 | 7.1e-29 |
| ma.L2 | 0.069 | 0.101 | 0.69 | 0.4912 |
| ma.L3 | 0.026 | 0.105 | 0.25 | 0.8031 |
| ma.L4 | -0.054 | 0.077 | -0.71 | 0.4767 |
| ar.S.L7 | 0.919 | 0.036 | 25.48 | 3.4e-143 |
| ma.S.L7 | -0.837 | 0.094 | -8.93 | 4.1e-19 |
| sigma2 | 0.259 | 0.034 | 7.59 | 3.3e-14 |

### B+  (AIC 998.3, BIC 1023.2, log-likelihood -491.1, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -3.770 | 3.399 | -1.11 | 0.2673 |
| ma.L1 | -0.720 | 0.093 | -7.72 | 1.2e-14 |
| ma.L2 | -0.255 | 0.096 | -2.66 | 0.0078 |
| ma.L3 | 0.076 | 0.101 | 0.75 | 0.4508 |
| ma.L4 | 0.067 | 0.087 | 0.77 | 0.4394 |
| ar.S.L7 | 0.914 | 0.038 | 23.76 | 7.7e-125 |
| ma.S.L7 | -0.813 | 0.081 | -10.08 | 6.9e-24 |
| sigma2 | 19.980 | 2.551 | 7.83 | 4.8e-15 |

### B-  (AIC 535.0, BIC 559.9, log-likelihood -259.5, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -0.466 | 0.380 | -1.23 | 0.2196 |
| ma.L1 | -1.452 | 473.662 | -0.00 | 0.9976 |
| ma.L2 | -0.201 | 132.096 | -0.00 | 0.9988 |
| ma.L3 | 0.224 | 199.136 | 0.00 | 0.9991 |
| ma.L4 | 0.209 | 93.215 | 0.00 | 0.9982 |
| ar.S.L7 | 0.939 | 0.038 | 24.97 | 1.4e-137 |
| ma.S.L7 | -0.891 | 0.093 | -9.60 | 8.3e-22 |
| sigma2 | 0.617 | 275.875 | 0.00 | 0.9982 |

### O+  (AIC 1220.3, BIC 1245.3, log-likelihood -602.2, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -0.927 | 4.870 | -0.19 | 0.8490 |
| ma.L1 | -0.924 | 0.228 | -4.04 | 5.2e-05 |
| ma.L2 | -0.351 | 0.109 | -3.21 | 0.0013 |
| ma.L3 | 0.029 | 0.103 | 0.28 | 0.7809 |
| ma.L4 | 0.079 | 0.113 | 0.69 | 0.4874 |
| ar.S.L7 | 0.955 | 0.029 | 32.62 | 2.5e-233 |
| ma.S.L7 | -0.984 | 0.436 | -2.26 | 0.0241 |
| sigma2 | 52.439 | 24.808 | 2.11 | 0.0345 |

### O-  (AIC 713.8, BIC 738.8, log-likelihood -348.9, converged: True)

| Parameter | Coef | Std err | z | p |
|---|---|---|---|---|
| dengue_season | -2.728 | 0.917 | -2.97 | 0.0029 |
| ma.L1 | -0.610 | 0.072 | -8.48 | 2.3e-17 |
| ma.L2 | -0.508 | 0.094 | -5.39 | 7.2e-08 |
| ma.L3 | 0.198 | 0.088 | 2.25 | 0.0243 |
| ma.L4 | 0.075 | 0.077 | 0.98 | 0.3294 |
| ar.S.L7 | 0.742 | 0.119 | 6.24 | 4.5e-10 |
| ma.S.L7 | -0.591 | 0.172 | -3.44 | 0.0006 |
| sigma2 | 3.730 | 0.383 | 9.74 | 2.0e-22 |

### Estimation quality: read before trusting the coefficients above

* **Degenerate estimates for A-, B-.** These fits report `converged: True` but contain standard errors in
  the hundreds (for example `sigma2` and `ma.S.L7` for A-, `ma.L1..L4` for B-). The optimiser stopped at a boundary; the
  coefficients for these two types are **not reliably identified**, even though the live system would accept them, since it
  only checks the convergence flag.
* **Non-invertible MA coefficients (|theta| >= 1):** A+ ma.S.L7=-1.05, B- ma.L1=-1.45. The live
  configuration sets `enforce_invertibility=False`, so these are permitted.
* **Seasonal AR and seasonal MA nearly cancel.** `ar.S.L7` is 0.74-0.99 and `ma.S.L7` is -0.59 to -1.05 across types.
  A seasonal AR(1) close to 1 paired with a seasonal MA(1) close to -1 is close to a common-factor cancellation, which
  usually signals an over-parameterised seasonal part.
* MA(2), MA(3), MA(4) are not significant for most types (see tables), consistent with the Phase 1 reading that q = 4 is
  more than the ACF suggests.

## 2. Candidate comparison (AIC / BIC, lower is better)

All models use the same data, the same dengue regressor and the same fit settings; all have d = 1 so AIC/BIC are comparable.
Alternatives: A `(0,1,1)x(0,0,0,7)`, B `(0,1,4)x(0,0,0,7)`, C `(0,1,1)x(1,0,1,7)`, D `(1,1,1)x(1,0,1,7)`, E `(0,1,2)x(1,0,1,7)`.

| Type | fixed | A | B | C | D | E | Best AIC | Best BIC |
|---|---|---|---|---|---|---|---|---|
| A+ | 970.6 / 995.6 | 1046.3 / 1055.8 | 1031.8 / 1050.8 | 982.1 / 997.8 | 982.5 / 1001.3 | 975.9 / 994.7 | fixed | E |
| A- | 507.0 / 532.0 | 545.5 / 555.0 | 535.6 / 554.6 | 508.2 / 523.9 | 510.1 / 528.9 | 509.6 / 528.3 | fixed | C |
| AB+ | 583.5 / 608.4 | 644.1 / 653.6 | 634.2 / 653.1 | 588.9 / 604.6 | 590.9 / 609.7 | 586.0 / 604.8 | fixed | C |
| AB- | 273.7 / 298.6 | 318.8 / 328.4 | 313.1 / 332.0 | 275.7 / 291.4 | 275.9 / 294.8 | 269.2 / 288.0 | E | E |
| B+ | 998.3 / 1023.2 | 1091.4 / 1100.9 | 1062.8 / 1081.7 | 1017.8 / 1033.5 | 1017.8 / 1036.7 | 1009.4 / 1028.2 | fixed | fixed |
| B- | 535.0 / 559.9 | 590.3 / 599.8 | 566.3 / 585.2 | 546.5 / 562.2 | 546.0 / 564.8 | 542.4 / 561.2 | fixed | fixed |
| O+ | 1220.3 / 1245.3 | 1322.5 / 1332.1 | 1280.5 / 1299.5 | 1245.2 / 1260.8 | 1237.6 / 1256.5 | 1229.7 / 1248.5 | fixed | fixed |
| O- | 713.8 / 738.8 | 785.2 / 794.7 | 747.0 / 766.0 | 740.7 / 756.4 | 737.8 / 756.6 | 727.9 / 746.7 | fixed | fixed |

* **AIC:** the fixed order has the lowest AIC in **7 of 8** types (A+, A-, AB+, B+, B-, O+, O-). Where it does not, the winner is
  AB-: E.
* **BIC (heavier complexity penalty):** the fixed order is best in only **4 of 8** types (B+, B-, O+, O-); simpler seasonal
  models win the others (A+: E, A-: C, AB+: C, AB-: E).
* Models with **no seasonal terms (A, B) are far worse** on AIC, by roughly 29 to 64 AIC points, for every type. The seasonal
  part earns its place even though it is over-parameterised.

**What this evidences:** the fixed order is a reasonable choice, clearly better than non-seasonal models, and lowest-AIC in
most types. It is **not** uniquely best: BIC prefers a smaller seasonal model for half the types, and the MA(3)/MA(4) terms
add little. "Reasonable and defensible" is supported; "optimal" is not.

## 3. THE DENGUE QUESTION

**First, what the data looks like.** The brief for this dataset says it has a graded seasonal curve with no binary step
at 1 June, unlike the older demo file. Checking that directly:

| Type | Mean 7d before 1 Jun | Mean 7d after 1 Jun | Change | Monthly means Mar / Apr / May / Jun / Jul / Aug / Sep |
|---|---|---|---|---|
| A+ | 92.3 | 89.3 | -3.3% | 100.0 / 95.3 / 93.1 / 89.1 / 81.9 / 70.8 / 66.2 |
| A- | 22.3 | 21.9 | -1.9% | 23.5 / 22.6 / 22.5 / 21.4 / 19.4 / 17.0 / 15.7 |
| AB+ | 30.1 | 29.6 | -1.9% | 31.0 / 30.7 / 30.4 / 29.4 / 26.1 / 23.3 / 21.6 |
| AB- | 10.6 | 10.1 | -4.1% | 11.0 / 11.0 / 11.0 / 10.1 / 9.3 / 8.2 / 7.6 |
| B+ | 105.9 | 100.6 | -5.0% | 109.0 / 105.3 / 106.4 / 100.8 / 91.7 / 80.7 / 74.1 |
| B- | 25.6 | 24.6 | -3.9% | 27.0 / 26.0 / 25.8 / 24.2 / 22.1 / 19.3 / 17.8 |
| O+ | 146.0 | 141.0 | -3.4% | 150.0 / 149.0 / 145.1 / 139.1 / 127.9 / 113.5 / 104.4 |
| O- | 34.3 | 30.7 | -10.4% | 35.5 / 33.1 / 32.6 / 30.9 / 28.9 / 25.3 / 23.3 |

Confirmed: **no binary step at 1 June.** The change across the boundary is only a few percent, and every type declines
smoothly, accelerating through July-September. It is a graded decline, not a season with a visible peak and recovery: the
180 days cover only March-September, so the series shows the downward leg and nothing else.

**Result, live binary flag, fixed order:**

| Type | Dengue coef | Std err | p | p < 0.05 | AIC with minus without | BIC with minus without |
|---|---|---|---|---|---|---|
| A+ | -1.792 | 2.425 | 0.4598 | no | +1.5 | +4.6 |
| A- | -0.029 | 0.388 | 0.9399 | no | +2.0 | +5.1 |
| AB+ | 0.116 | 0.996 | 0.9070 | no | +2.0 | +5.1 |
| AB- | -0.293 | 0.313 | 0.3497 | no | +1.0 | +4.1 |
| B+ | -3.770 | 3.399 | 0.2673 | no | -0.3 | +2.8 |
| B- | -0.466 | 0.380 | 0.2196 | no | +1.4 | +4.5 |
| O+ | -0.927 | 4.870 | 0.8490 | no | +2.0 | +5.1 |
| O- | -2.728 | 0.917 | 0.0029 | YES | -3.6 | -0.5 |

* **Significant at 5% for 1 of 8 types: O-.** The other 7 are not significant (p from
  0.22 upward). That is a legitimate finding and it is reported as found.
* For O-, p = 0.0029, which also survives a Bonferroni correction across 8 tests (threshold 0.00625).
  The sign is negative (lower stock while the flag is on), in the direction the hypothesis predicts.
* Adding the regressor **lowers AIC for only 2 type(s) (B+, O-) and lowers BIC for 1 (O-).**
  For most types the extra parameter buys no fit. Across the five alternative orders the dengue p-value stays non-significant
  for the same types, and is significant only for O- (A, B, C, D).

**How to read it.** Inside this sample the flag switches exactly once (0 for 63 days, then 1 for 117), and it does so while
every series is already trending downward. A single switch that coincides with a declining trend is a weak basis for
attributing a coefficient to seasonality, and this report cannot separate the two. The honest summary is:
*the pipeline detects a flag-associated shift in one blood type of eight; it does not demonstrate a dengue-driven seasonal
effect.* Because the series is generated, it also cannot say anything about real dengue and real demand.

## Limitations

* Optimiser stability (above) limits what can be read from A- and B-.
* Five alternatives is a small candidate set, not an exhaustive search.
* No adjustment was made for the seasonal AR/MA near-cancellation.
