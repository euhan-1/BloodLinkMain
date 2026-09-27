# Phase 2: Estimation

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the last 30 days) — what the 36-candidate grid search and its AIC/Ljung-Box criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither stands in for the other.

## What the live system does versus what this report does

The live selection mechanism ranks all 36 grid candidates by AIC and never looks at a coefficient table — the numbers
below are read off afterward, for this report, not consulted by the system itself. SARIMAX here is a regression with
SARIMA errors (the dengue exog is NOT differenced; it enters as `y_t = beta * flag_t + eta_t`, with `eta_t` following
the SARIMA structure).

## 1. Selected order per type (SELECTION FIT: training window, ending 2026-08-27)

| Type | Selected order | AIC | AIC gap to runner-up | Dengue coef | Dengue p | In-sample LB criterion met |
|---|---|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | 2007.2 | - | -6.936 | 1.8e-32 | yes |
| A- | (1,1,2)x(1,0,1,7) | 969.5 | 0.44 | -2.308 | 6.7e-48 | no |
| AB+ | (1,1,2)x(1,0,1,7) | 1083.3 | 1.26 | -1.510 | 9.4e-11 | no |
| AB- | (1,1,1)x(1,0,1,7) | 561.0 | 2.00 | -0.589 | 4.0e-12 | yes |
| B+ | (1,1,2)x(1,0,1,7) | 1797.1 | 2.00 | -4.773 | 6.5e-09 | yes |
| B- | (1,1,2)x(1,0,1,7) | 753.1 | 1.87 | -1.121 | 6.5e-07 | yes |
| O+ | (1,1,2)x(1,0,1,7) | 2210.0 | 1.42 | -14.229 | 1.8e-33 | yes |
| O- | (1,1,2)x(1,0,1,7) | 1253.2 | 1.72 | -5.717 | 9.9e-131 | yes |

"AIC gap to runner-up" is relative to the winner, not always the raw lowest-AIC candidate in the grid: the selection
rule picks the lowest AIC AMONG CANDIDATES THAT PASS Ljung-Box, so a lower-AIC candidate that fails the criterion is
skipped. **This happens for A+** — see section 2's top-5 table for where the selected order actually ranks by raw AIC; the better-AIC candidate(s) above it in that table failed Ljung-Box.
Every gap shown is under 2.0 — none of these wins are dominant even within their own pool; several other candidates
are statistically indistinguishable by AIC alone (a gap under ~2 is conventionally read as "not much evidence
favouring one over the other"). A+ has no gap to report (`-`) because it is the ONLY one of 36 candidates that passes Ljung-Box in-sample; there is no runner-up within its own pool at all — a narrower margin than any other type. The Ljung-Box criterion is what actually separates these
candidates (PHASE_3).

## 2. Each type's own top-5 candidates (replaces the old fixed 5-alternative comparison)

The previous version of this document compared the single fixed order against 5 abstract alternative orders shared
across all 8 types. Per-type order selection makes that comparison meaningless — each type already ran an exhaustive
36-candidate search of its own. This table shows the 5 best (by AIC) candidates from THAT search, per type.

| Type | Rank | Order | AIC | Gap to winner |
|---|---|---|---|---|
| A+ | 1 | (1,1,2)x(1,0,1,7) | 1999.4 | 0.00 |
| A+ | 2 | (2,1,2)x(1,0,1,7) | 2001.4 | 2.00 |
| A+ | 3 | (1,1,1)x(1,0,1,7) | 2007.2 | 7.77 |
| A+ | 4 | (2,1,1)x(1,0,1,7) | 2009.2 | 9.77 |
| A+ | 5 | (0,1,2)x(1,0,1,7) | 2017.5 | 18.09 |
| A- | 1 | (1,1,2)x(1,0,1,7) | 969.5 | 0.00 |
| A- | 2 | (2,1,2)x(1,0,1,7) | 969.9 | 0.44 |
| A- | 3 | (1,1,1)x(1,0,1,7) | 970.8 | 1.28 |
| A- | 4 | (2,1,1)x(1,0,1,7) | 972.8 | 3.28 |
| A- | 5 | (0,1,2)x(1,0,1,7) | 989.9 | 20.37 |
| AB+ | 1 | (1,1,2)x(1,0,1,7) | 1083.3 | 0.00 |
| AB+ | 2 | (1,1,1)x(1,0,1,7) | 1084.6 | 1.26 |
| AB+ | 3 | (2,1,2)x(1,0,1,7) | 1085.3 | 1.97 |
| AB+ | 4 | (2,1,1)x(1,0,1,7) | 1086.4 | 3.10 |
| AB+ | 5 | (0,1,2)x(1,0,1,7) | 1106.7 | 23.40 |
| AB- | 1 | (1,1,1)x(1,0,1,7) | 561.0 | 0.00 |
| AB- | 2 | (2,1,1)x(1,0,1,7) | 563.0 | 2.00 |
| AB- | 3 | (1,1,2)x(1,0,1,7) | 565.3 | 4.35 |
| AB- | 4 | (2,1,2)x(1,0,1,7) | 567.2 | 6.25 |
| AB- | 5 | (0,1,2)x(1,0,1,7) | 568.6 | 7.62 |
| B+ | 1 | (1,1,2)x(1,0,1,7) | 1797.1 | 0.00 |
| B+ | 2 | (2,1,2)x(1,0,1,7) | 1799.1 | 2.00 |
| B+ | 3 | (1,1,1)x(1,0,1,7) | 1799.5 | 2.43 |
| B+ | 4 | (2,1,1)x(1,0,1,7) | 1801.5 | 4.41 |
| B+ | 5 | (2,1,0)x(1,0,1,7) | 1853.3 | 56.21 |
| B- | 1 | (1,1,2)x(1,0,1,7) | 753.1 | 0.00 |
| B- | 2 | (2,1,2)x(1,0,1,7) | 755.0 | 1.87 |
| B- | 3 | (1,1,1)x(1,0,1,7) | 755.0 | 1.92 |
| B- | 4 | (2,1,1)x(1,0,1,7) | 757.0 | 3.91 |
| B- | 5 | (0,1,2)x(1,0,1,7) | 768.6 | 15.51 |
| O+ | 1 | (1,1,2)x(1,0,1,7) | 2210.0 | 0.00 |
| O+ | 2 | (2,1,2)x(1,0,1,7) | 2211.5 | 1.42 |
| O+ | 3 | (1,1,1)x(1,0,1,7) | 2213.3 | 3.24 |
| O+ | 4 | (2,1,1)x(1,0,1,7) | 2215.3 | 5.23 |
| O+ | 5 | (0,1,2)x(1,0,1,7) | 2223.5 | 13.47 |
| O- | 1 | (1,1,2)x(1,0,1,7) | 1253.2 | 0.00 |
| O- | 2 | (2,1,2)x(1,0,1,7) | 1255.0 | 1.72 |
| O- | 3 | (1,1,1)x(1,0,1,7) | 1255.9 | 2.61 |
| O- | 4 | (2,1,1)x(1,0,1,7) | 1257.5 | 4.26 |
| O- | 5 | (0,1,2)x(1,0,1,7) | 1273.8 | 20.59 |

## 3. THE DENGUE QUESTION

**Significant (p < 0.05) for 8 of 8 types on the SELECTION FIT** (A+, A-, AB+, AB-, B+, B-, O+, O-),
and **8 of 8 on the PRODUCTION FIT** (full series) — see the table below. This is a marked change from
earlier versions of this analysis (on the old 180-day, single-dengue-season, monotonic-decline series, only 0-1 of 8
types were significant). This 420-day series covers two full dengue seasons with no monotonic drift, so the flag is no
longer confounded with an unrelated trend the way it was before — and every coefficient is negative (lower stock while
the flag is on), in the direction the hypothesis predicts, with p-values as small as 1e-131 for some types. **Report
this as found: it is a property of this generated series having two clean seasonal cycles, not proof the underlying
mechanism is real** — this is still demonstration data (see the data notice above).

| Type | Dengue coef (production fit) | Dengue p (production fit) |
|---|---|---|
| A+ | -7.058 | 1.5e-36 |
| A- | -2.304 | 9.1e-51 |
| AB+ | -1.524 | 1.3e-13 |
| AB- | -0.578 | 3.6e-11 |
| B+ | -4.742 | 5.7e-10 |
| B- | -1.101 | 3.4e-12 |
| O+ | -14.745 | 6.7e-33 |
| O- | -5.719 | 8.7e-152 |

## 4. Selection stability check

Selection was re-run with the training window cut a further 30 days shorter (ending
2026-07-28 instead of 2026-08-27), to see whether the winning order for each
type is sensitive to exactly where the window ends.

| Type | Order (window ending 2026-08-27) | Order (window ending 2026-07-28) | Stable? |
|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **NO — flips** |
| A- | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |
| AB+ | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |
| AB- | (1,1,1)x(1,0,1,7) | (1,1,1)x(1,0,1,7) | **yes** |
| B+ | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |
| B- | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |
| O+ | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |
| O- | (1,1,2)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **yes** |

**7 of 8 types select the SAME order under both windows.** **A+ flips** (A+: (1,1,1)x(1,0,1,7) -> (1,1,2)x(1,0,1,7)). This is reported as a finding, not smoothed over: for at least one type, the selected order depends on exactly where the training window happens to end, which is a real limitation of picking a single point estimate from AIC + Ljung-Box on one window.

## Limitations

* AIC gaps under ~2 mean several candidates per type are close competitors; the grid search still picks exactly one.
* The stability check moves the window by only one horizon (30 days); it does not test sensitivity to the window
  LENGTH, only its END point, and only at one alternative end point.
* Per-type full coefficient tables (MA/AR terms, not just the dengue regressor) are not shown here.
