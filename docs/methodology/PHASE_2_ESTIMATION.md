# Phase 2: Estimation

> **Data notice.** The 420-day series analysed here (`northside_420d_history.csv`, 2025-08-03 to 2026-09-26, 8 blood types, 3,360 daily counts) is **generated demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file (`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been removed. This report shows that the implementation performs the methodology correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.

> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the last 30 days) — what the 72-candidate grid search (d in {0,1} searched) and its AIC/Ljung-Box criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither stands in for the other.

## What the live system does versus what this report does

The live selection mechanism ranks all 72 grid candidates by AIC and never looks at a coefficient table — the numbers
below are read off afterward, for this report, not consulted by the system itself. SARIMAX here is a regression with
SARIMA errors (the dengue exog is NOT differenced; it enters as `y_t = beta * flag_t + eta_t`, with `eta_t` following
the SARIMA structure).

## 1. Selected order per type (SELECTION FIT: training window, ending 2026-08-27)

| Type | Selected order | AIC | AIC gap to runner-up | Dengue coef | Dengue p | In-sample LB criterion met |
|---|---|---|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | 2007.2 | 1.24 | -6.936 | 1.8e-32 | yes |
| A- | (2,0,0)x(1,0,1,7) | 960.7 | 0.26 | -2.302 | 2.6e-45 | no |
| AB+ | (1,0,2)x(1,0,1,7) | 1076.0 | 0.22 | -1.522 | 1.4e-12 | no |
| AB- | (1,0,1)x(1,0,1,7) | 559.5 | 0.24 | -0.566 | 7.2e-13 | yes |
| B+ | (1,1,2)x(1,0,1,7) | 1797.1 | 0.72 | -4.773 | 6.5e-09 | yes |
| B- | (1,0,1)x(1,0,1,7) | 743.0 | 0.39 | -1.123 | 1.4e-12 | yes |
| O+ | (1,0,2)x(1,0,1,7) | 2204.9 | 3.50 | -14.273 | 2.7e-50 | yes |
| O- | (0,0,2)x(1,0,1,7) | 1243.4 | 3.44 | -5.724 | 5.5e-162 | yes |

"AIC gap to runner-up" is relative to the winner, not always the raw lowest-AIC candidate in the grid: the selection
rule picks the lowest AIC AMONG CANDIDATES THAT PASS Ljung-Box, so a lower-AIC candidate that fails the criterion is
skipped. **This happens for A+** — see section 2's top-5 table for where the selected order actually ranks by raw AIC; the better-AIC candidate(s) above it in that table failed Ljung-Box.
A+, A-, AB+, AB-, B+, B- have a gap under 2.0 — not dominant wins even within their own pool; a gap under ~2 is conventionally read as not much evidence favouring one candidate over another. O+, O- clear their runner-up by more (up to 3.5 points for O+) — a real preference for that specific order within the passing pool, not just noise. 
The Ljung-Box criterion is what actually separates these candidates (PHASE_3).

## 2. Each type's own top-5 candidates (replaces the old fixed 5-alternative comparison)

The previous version of this document compared the single fixed order against 5 abstract alternative orders shared
across all 8 types. Per-type order selection makes that comparison meaningless — each type already ran an exhaustive
72-candidate search of its own (d in {0,1} included). This table shows the 5 best (by AIC) candidates from THAT search, per type.

| Type | Rank | Order | AIC | Gap to winner |
|---|---|---|---|---|
| A+ | 1 | (1,1,2)x(1,0,1,7) | 1999.4 | 0.00 |
| A+ | 2 | (2,1,2)x(1,0,1,7) | 2001.4 | 2.00 |
| A+ | 3 | (1,0,2)x(1,0,1,7) | 2003.8 | 4.40 |
| A+ | 4 | (2,0,2)x(1,0,1,7) | 2006.2 | 6.77 |
| A+ | 5 | (1,1,1)x(1,0,1,7) | 2007.2 | 7.77 |
| A- | 1 | (2,0,0)x(1,0,1,7) | 960.7 | 0.00 |
| A- | 2 | (1,0,1)x(1,0,1,7) | 961.0 | 0.26 |
| A- | 3 | (0,0,2)x(1,0,1,7) | 961.9 | 1.16 |
| A- | 4 | (2,0,1)x(1,0,1,7) | 962.5 | 1.75 |
| A- | 5 | (2,0,2)x(1,0,1,7) | 963.5 | 2.79 |
| AB+ | 1 | (1,0,2)x(1,0,1,7) | 1076.0 | 0.00 |
| AB+ | 2 | (1,0,1)x(1,0,1,7) | 1076.2 | 0.22 |
| AB+ | 3 | (2,0,0)x(1,0,1,7) | 1076.2 | 0.23 |
| AB+ | 4 | (1,0,0)x(1,0,1,7) | 1077.2 | 1.19 |
| AB+ | 5 | (2,0,1)x(1,0,1,7) | 1078.7 | 2.68 |
| AB- | 1 | (1,0,1)x(1,0,1,7) | 559.5 | 0.00 |
| AB- | 2 | (2,0,0)x(1,0,1,7) | 559.8 | 0.24 |
| AB- | 3 | (1,0,0)x(1,0,1,7) | 560.8 | 1.29 |
| AB- | 4 | (1,1,1)x(1,0,1,7) | 561.0 | 1.43 |
| AB- | 5 | (0,0,1)x(1,0,1,7) | 562.9 | 3.35 |
| B+ | 1 | (1,1,2)x(1,0,1,7) | 1797.1 | 0.00 |
| B+ | 2 | (1,0,1)x(1,0,1,7) | 1797.8 | 0.72 |
| B+ | 3 | (2,0,0)x(1,0,1,7) | 1797.9 | 0.78 |
| B+ | 4 | (2,1,2)x(1,0,1,7) | 1799.1 | 2.00 |
| B+ | 5 | (2,0,2)x(1,0,1,7) | 1799.3 | 2.17 |
| B- | 1 | (1,0,1)x(1,0,1,7) | 743.0 | 0.00 |
| B- | 2 | (2,0,2)x(1,0,1,7) | 743.4 | 0.39 |
| B- | 3 | (2,0,0)x(1,0,1,7) | 743.5 | 0.56 |
| B- | 4 | (2,0,1)x(1,0,1,7) | 744.7 | 1.72 |
| B- | 5 | (1,0,0)x(1,0,1,7) | 745.6 | 2.66 |
| O+ | 1 | (1,0,2)x(1,0,1,7) | 2204.9 | 0.00 |
| O+ | 2 | (1,0,1)x(1,0,1,7) | 2208.4 | 3.50 |
| O+ | 3 | (2,0,0)x(1,0,1,7) | 2208.4 | 3.50 |
| O+ | 4 | (1,1,2)x(1,0,1,7) | 2210.0 | 5.15 |
| O+ | 5 | (2,0,1)x(1,0,1,7) | 2210.4 | 5.50 |
| O- | 1 | (0,0,2)x(1,0,1,7) | 1243.4 | 0.00 |
| O- | 2 | (2,0,0)x(1,0,1,7) | 1246.9 | 3.44 |
| O- | 3 | (1,0,1)x(1,0,1,7) | 1247.0 | 3.59 |
| O- | 4 | (2,0,1)x(1,0,1,7) | 1248.9 | 5.47 |
| O- | 5 | (2,0,2)x(1,0,1,7) | 1250.1 | 6.64 |

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
| A- | -2.298 | 1.5e-48 |
| AB+ | -1.525 | 3.4e-14 |
| AB- | -0.557 | 6.7e-11 |
| B+ | -4.742 | 5.7e-10 |
| B- | -1.102 | 1.3e-14 |
| O+ | -14.147 | 9.7e-31 |
| O- | -5.721 | 1.8e-176 |

## 4. Selection stability check

Selection was re-run with the training window cut a further 30 days shorter (ending
2026-07-28 instead of 2026-08-27), to see whether the winning order for each
type is sensitive to exactly where the window ends.

| Type | Order (window ending 2026-08-27) | Order (window ending 2026-07-28) | Stable? |
|---|---|---|---|
| A+ | (1,1,1)x(1,0,1,7) | (1,1,2)x(1,0,1,7) | **NO — flips** |
| A- | (2,0,0)x(1,0,1,7) | (1,0,2)x(1,0,1,7) | **NO — flips** |
| AB+ | (1,0,2)x(1,0,1,7) | (1,0,0)x(1,0,1,7) | **NO — flips** |
| AB- | (1,0,1)x(1,0,1,7) | (2,0,0)x(1,0,1,7) | **NO — flips** |
| B+ | (1,1,2)x(1,0,1,7) | (1,0,2)x(1,0,1,7) | **NO — flips** |
| B- | (1,0,1)x(1,0,1,7) | (1,0,1)x(1,0,1,7) | **yes** |
| O+ | (1,0,2)x(1,0,1,7) | (1,0,1)x(1,0,1,7) | **NO — flips** |
| O- | (0,0,2)x(1,0,1,7) | (1,0,2)x(1,0,1,7) | **NO — flips** |

**1 of 8 types select the SAME order under both windows.** **A+, A-, AB+, AB-, B+, O+, O- flip** (A+: (1,1,1)x(1,0,1,7) -> (1,1,2)x(1,0,1,7); A-: (2,0,0)x(1,0,1,7) -> (1,0,2)x(1,0,1,7); AB+: (1,0,2)x(1,0,1,7) -> (1,0,0)x(1,0,1,7); AB-: (1,0,1)x(1,0,1,7) -> (2,0,0)x(1,0,1,7); B+: (1,1,2)x(1,0,1,7) -> (1,0,2)x(1,0,1,7); O+: (1,0,2)x(1,0,1,7) -> (1,0,1)x(1,0,1,7); O-: (0,0,2)x(1,0,1,7) -> (1,0,2)x(1,0,1,7)). Full-order stability is low, but **d specifically is far steadier: 7 of 8 types keep the SAME d across both windows** (A+, A-, AB+, AB-, B-, O+, O-); only B+ changes d (B+: d=1 -> d=0). The instability is mostly in p/q, not in the more consequential integrated-vs-stationary choice. This is reported as a finding, not smoothed over: for most types the exact order depends on where the training window ends, which is a real limitation of picking a single point estimate from AIC + Ljung-Box on one window.

## 5. What the instability actually costs the forecast

The order label above is unstable mainly because the AIC surface is flat: section 1 already shows most types' winning
candidate clears its runner-up by well under 2 AIC points, i.e. many of the 72 candidates sit within a few points of
each other. That alone does not say how much the CHOICE of order matters downstream. To isolate that, order_b (the
order selected on the window ending 2026-07-28) was refit on order_a's OWN training data (window
ending 2026-08-27) — same data as the SELECTION FIT above, different order — and used to forecast
the identical 30-day hold-out, so the two forecasts differ only in which order was used.

| Type | MAPE % (order_a) | RMSE (order_a) | MAPE % (order_b) | RMSE (order_b) | Mean abs diff (units) | Mean abs diff (% of type mean) | Max abs diff (units) | Max abs diff (% of type mean) |
|---|---|---|---|---|---|---|---|---|
| A+ | 4.53 | 3.25 | 4.51 | 3.25 | 0.02 | 0.03% | 0.05 | 0.07% |
| A- | 4.32 | 0.84 | 4.64 | 0.87 | 0.23 | 1.38% | 0.61 | 3.59% |
| AB+ | 4.19 | 0.96 | 4.19 | 0.96 | 0.01 | 0.04% | 0.02 | 0.08% |
| AB- | 7.50 | 0.67 | 7.51 | 0.67 | 0.00 | 0.02% | 0.01 | 0.09% |
| B+ | 4.02 | 2.69 | 4.69 | 3.15 | 1.35 | 2.58% | 2.53 | 4.83% |
| B- | 6.79 | 0.82 | 6.79 | 0.82 | 0.00 | 0.00% | 0.00 | 0.00% |
| O+ | 5.35 | 6.23 | 5.32 | 6.20 | 0.06 | 0.07% | 0.22 | 0.25% |
| O- | 5.71 | 1.48 | 6.41 | 1.65 | 0.76 | 3.03% | 1.55 | 6.22% |

A+, AB+, AB-, B-, O+ move the 30-day forecast by under 0.1%
on average between the two orders — **5 of 8 types**, where the label instability documented above is
close to cosmetic. It is not negligible for **O-, B+, A-**, where mean divergence runs from
1.4% to 3.0%
and MAPE moves by up to 0.7 points between the two orders (B+: 0.67 points, O-: 0.69 points).

**The verdict-flip pattern does not line up with the divergence sizes, and that is reported as found, not smoothed
over.** Restricting to the 6 types where order_a itself satisfies the Ljung-Box criterion on its own
window (A+, AB-, B+, B-, O+, O- — A-, AB+ are excluded here because order_a
never passed to begin with, so there is no pass/fail verdict for order_b to flip against): order_b, refit on order_a's
window, still passes for **3 of 6** (AB-, B-, O+) and
fails for **3** (A+, B+, O-). Only B+, O-
are in both that failing set AND the high-divergence set above — A+
flips the verdict with barely any forecast movement (A+: 0.03%),
and A- sits among the highest-divergence
types without any verdict to flip at all (A-: 1.38%).
A verdict flip and a large forecast shift are two different failure modes here, not one.

| Type | order_a passes on its own window | order_b passes on order_a's window | Verdict |
|---|---|---|---|
| A+ | yes | no | **flips** |
| A- | no (excluded above) | no | n/a |
| AB+ | no (excluded above) | no | n/a |
| AB- | yes | yes | stable |
| B+ | yes | no | **flips** |
| B- | yes | yes | stable |
| O+ | yes | yes | stable |
| O- | yes | no | **flips** |

That order_a passes its own window is itself partly guaranteed rather than an independent confirmation: it was
SELECTED on that window using that exact criterion, among candidates that pass it (section 1). The informative number
is the one above — of the 6 types where a passing order exists at all, an order picked on a window
30 days earlier still clears the same bar on the later window for 3 and fails
it for 3.

## Limitations

* AIC gaps under ~2 mean several candidates per type are close competitors; the grid search still picks exactly one.
* The stability check moves the window by only one horizon (30 days); it does not test sensitivity to the window
  LENGTH, only its END point, and only at one alternative end point.
* Per-type full coefficient tables (MA/AR terms, not just the dengue regressor) are not shown here.
* Section 5's instability-cost figures are a single 30-day-earlier comparison per type, not a distribution over many
  window cuts — the same single-alternative-end-point limitation as the stability check itself, applied to cost rather
  than to the raw order label. This mirrors the general finding in Abdallah et al. (2022), already cited in this
  review, that the best-fitting model on one data window is not guaranteed to remain best on a later one; nothing here
  says this generated series' specific pass/fail and divergence pattern would repeat on a different window cut or on
  real facility data.
