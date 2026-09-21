# Synthetic-Data SARIMAX Validation — Reference Summary

**Purpose.** Real historical inventory data isn't available yet (pending an
unapproved facility interview), so the SARIMAX forecasting approach was
validated end-to-end on a generated, deterministic synthetic dataset first —
following the project's standard Box-Jenkins staged methodology
(Identification → Estimation → Diagnostic Checking → Forecasting), with an
explicit rule to go back to Identification whenever a diagnostic check
fails. Every number below comes from `synthetic_inventory_snapshots`, a
table that is deliberately separate from the real `inventory_snapshots`
table and carries no `facility_id` — synthetic and real data are never
comingled at the schema level.

Scripts referenced below all live in `server/` and are safe to re-run
(deterministic, seed 42).

---

## Real-data path: prediction intervals, not a second SARIMAX model (2026-08-23)

**This section is about the *other* forecast path** — the plain linear trend
`GET /forecast` fits on a facility's own real `inventory_snapshots` rows once
enough exist, completely separate from the synthetic SARIMAX model this
document otherwise covers. It's recorded here anyway because the reasoning
was developed alongside this doc's methodology and belongs with the rest of
the forecast's honesty record, not scattered only across chat history.

**The problem.** The real-trend path projects 30 days out
(`FORECAST_CHECKPOINTS`) from a line fit on as few as `MIN_DAYS_REQUIRED`
points. With the original `MIN_DAYS_REQUIRED = 7`, that's extrapolating
30/7 ≈ 4.3x past the fitted range on a slope estimated from only 5 residual
degrees of freedom — a large, unacknowledged extrapolation relative to the
data it's based on. The point forecast was never wrong to compute, but
showing it with no uncertainty indicator implied a confidence the underlying
n didn't support.

**Options considered:**
1. *Raise the threshold* (e.g. 14 or 21 days) — doesn't fix the problem, just
   delays it; even at 21 days you're still extrapolating ~1.4x, and there's
   no principled way to justify one specific new number over another.
2. *Shrink the forecast horizon early, grow it as history accumulates* —
   directly addresses the extrapolation ratio, but needs its own arbitrary
   growth-rate constant and a UI that explains why the chart gets longer
   over time.
3. *Keep a low, mathematically-justified threshold and make the uncertainty
   explicit* — chosen. A real OLS prediction interval captures both "too few
   points" and "too much extrapolation distance" in one computed quantity
   that requires no tuning and improves automatically as real history grows.

**The formula**, applied to `_linear_trend`'s existing `(day_offset, units)`
points, for a forecast at a future offset `x0`:

```
half_width = t(df, confidence) * s * sqrt(1 + 1/n + (x0 - x̄)² / Sxx)

where:
  s   = sqrt(RSS / df)         residual standard error
  df  = n - 2                  residual degrees of freedom
  RSS = Σ(y_i - ŷ_i)²          sum of squared residuals from the fitted line
  x̄   = mean(x_i)              center of the historical window
  Sxx = Σ(x_i - x̄)²            spread of the historical window
```

Implemented as `_prediction_interval_half_width()` in `main.py`, using
`scipy.stats.t.ppf` for the exact critical value at whatever `df` the
facility currently has (no lookup table, no normal-approximation shortcut).
The `(x0 - x̄)²` term is what makes the interval widen the further a
checkpoint projects into the future; the `1/n` and `s` terms are what make
it narrow as more real days accumulate. Confidence is 95%
(`FORECAST_INTERVAL_CONFIDENCE`).

**Why `n ≥ 3`, not a round number.** This is the actual mathematical floor,
not a design preference: with only 2 points, a line fits them *exactly*
(zero residual), leaving `df = 0` — the residual variance the whole formula
is built on doesn't exist. `MIN_DAYS_REQUIRED` dropped from `7` to `3`
accordingly. Below 3 real days, the synthetic SARIMAX+dengue stand-in this
document validates is still what's shown — not because the real trend would
be "untrustworthy" (that job now belongs to the interval, continuously,
above the floor), but because a 3-point interval at `t(1, 0.975) ≈ 12.7` is
so wide it carries almost no information, while the synthetic stand-in at
least carries a plausible, clearly-labeled shape (weekly/seasonal dynamics)
for those first couple of days.

**Where it renders.** `GET /forecast`'s `series` entries now carry
`lower`/`upper` alongside `units` (each summed across blood types the same
way the point estimate already was), and the response carries
`interval_confidence` (`0.95`, or `null` on any path without a computable
band). The Dashboard's 30-Day Shortage Forecast chart renders these as
Recharts `ErrorBar` whiskers on each bar, with a one-line caption naming the
confidence level and explaining why the whiskers move. Verified live against
Northside Community Blood Center (6 real days on file, now correctly showing
`real_facility_history` where it previously fell back to synthetic at the
old 7-day threshold) — confirmed the switch fires on existing data with zero
manual data changes required.

---

## 0. Seasonal calibration (2026-08-21 revision)

The generator's dengue-season shape (timing, amplitude, rise/fall pattern)
was originally an *assumption* — a symmetric cosine dip, trough at mid-August,
92 days wide on each side, bottoming at 45% of baseline. This section
replaces that assumption with a shape calibrated against **real DOH
Epidemiology Bureau weekly dengue surveillance counts for Batangas province,
2016–2021**. To be precise about what "real" means here: **the seasonal
*shape* (when it rises, when it peaks, when it falls, how deep the dip is)
is now grounded in real surveillance data. The specific dates/years the
generator produces (2024–2027 in the current run) remain synthetic** — real
province-level *inventory* data for this system's actual demonstration
period isn't publicly available, only case-count surveillance data, and
only for one province, not the specific facilities this app models. This is
a calibration of shape, not a claim that the generated series itself is real.

**Data excluded from calibration, and why:** 2019 was a declared Philippine
national dengue epidemic (Aug–Sep 2019 weekly counts of 500–900+, vs.
150–240 in 2016–2018 — a 3–4x outlier); 2020–2021 collapsed to single-digit
weekly counts under COVID-19 lockdown-suppressed transmission and never
recovered within the dataset. Neither reflects a *typical* season. Calibration
uses 2016–2018 only.

**What changed:**

| | Previously assumed | Real Batangas data (2016–2018) |
|---|---|---|
| Peak | mid-August | **~Sep 10** |
| Shape | symmetric, 92 days each side | **asymmetric** — 84 days rise, 112 days fall (climbs faster than it falls) |
| Trough as fraction of baseline | 0.45 | **0.39** |

`generate_synthetic_forecast_data.py`'s `DENGUE_PEAK_MONTH_DAY`,
`SEASON_RISE_DAYS`/`SEASON_FALL_DAYS` (replacing the old single
`SEASON_HALF_WIDTH_DAYS`), and `SEASON_TROUGH_MULTIPLIER` now reflect these
values, and `_seasonal_multiplier()` uses independent half-widths for the
rising and falling sides of the season rather than one symmetric window.

**Bug found and fixed — phase inversion (independent of the calibration
above, and the more consequential of the two changes).** While building a
before/after comparison of the old vs. new shape, testing the *original,
unmodified* `_seasonal_multiplier()` directly showed it returned **1.0
(baseline) exactly at the nominal peak date**, and the **trough value at the
season edge** — the reverse of its own docstring ("cosine dip to
SEASON_TROUGH_MULTIPLIER at the peak") and of what every description of this
generator, including the pre-2026-08-21 version of this document, has always
claimed. Because distance-from-peak was computed as an unsigned value, the
practical effect wasn't just a shift — it produced a **double-dip shape**:
real troughs at ~92 days *before* and *after* the nominal peak date (~May 15
and ~Nov 15), with the actual peak date sitting at baseline in between. Both
of those trough dates fall **outside** `DENGUE_SEASON_MONTHS` (calendar
months 6–10) — the exact exogenous regressor Stage 3 onward regresses
against — so the entire pre-2026-08-21 Stages 3–6 estimation was fit against
data whose real seasonal lows didn't line up with the variable it was being
tested against. Root cause: an extraneous `(1 - frac_from_peak)` term in the
cosine formula; fixed by using `frac_from_peak` directly. This also affected
the live, currently-serving cached forecast (`fit_and_cache_synthetic_forecast.py`'s
output) prior to this revision, since it was fit on the same buggy data.

**Given the shape changed qualitatively (double-dip → single dip), not just
in amplitude, the full pipeline below was re-run end to end** rather than
patching only the affected numbers — see the "Bugs found and fixed,
consolidated" section for the complete before/after comparison.

---

## 1. Synthetic data generation

`generate_synthetic_forecast_data.py` produces a 2-year daily series (730
days) for all 8 blood types, with a seasonal demand spike modeled as
**elevated draw rate** during dengue season — this shows up as *lower
stock*, not a literal upward spike, matching the app's existing
shortage-alert language. Baseline levels are derived from the live
`blood_type_thresholds` table (`baseline = threshold × 1.4`), so synthetic
scale is denominated in the same real-world units as the rest of the system.

**Bug found and fixed (original, pre-seasonal-calibration):** the first
version of the generator used a plain additive random walk (fixed-delta
restocks, no mean reversion). Over 730 days this either drifted away
unboundedly or got clamped at zero — **19–27% of all days across every
blood type were sitting at exactly 0 units**, which is not a "healthy
baseline with a seasonal dip," it's chronic stockout. Fixed by redesigning
around a smooth seasonal target level that each periodic restock tops up
*relative to*, instead of an unconditional additive delta.

**Post-recalibration verification** (`verify_synthetic_forecast_data.py`,
re-run 2026-08-21): 0% zero-stock days confirmed again on the regenerated
series. The dengue-vs-non-dengue mean difference is now a **consistent
28–31% dip across all 8 types** — larger and visibly more consistent than
the pre-fix figure (11–18%), because `DENGUE_SEASON_MONTHS` (Jun–Oct) now
actually contains the real dip instead of straddling it:

| Type | Dengue-season mean | Non-season mean | Diff | Min | Max |
|---|---|---|---|---|---|
| A- | 46.1 | 65.8 | -30.0% | 19 | 93 |
| A+ | 75.7 | 108.0 | -29.9% | 26 | 151 |
| AB- | 23.4 | 32.9 | -29.0% | 7 | 47 |
| AB+ | 28.3 | 40.8 | -30.6% | 10 | 55 |
| B- | 37.8 | 53.4 | -29.2% | 11 | 73 |
| B+ | 58.6 | 81.6 | -28.1% | 17 | 111 |
| O- | 76.1 | 108.4 | -29.8% | 26 | 144 |
| O+ | 93.8 | 133.2 | -29.6% | 34 | 185 |

---

## 2. Identification (Stage 2) — re-run 2026-08-21

`identify_synthetic_forecast_data.py` — Augmented Dickey-Fuller test on the
raw series per blood type:

| Type | ADF stat | p-value | Stationary @5%? | d |
|---|---|---|---|---|
| O+ | -1.9428 | 0.31218 | No | 1 |
| O- | -2.0245 | 0.27593 | No | 1 |
| A+ | -1.9833 | 0.29394 | No | 1 |
| A- | -2.1252 | 0.23450 | No | 1 |
| B+ | -2.1617 | 0.22042 | No | 1 |
| B- | -2.2906 | 0.17501 | No | 1 |
| AB+ | -2.2084 | 0.20322 | No | 1 |
| AB- | -2.1770 | 0.21471 | No | 1 |

**This flipped from the pre-calibration result (d=0 for all 8 types) to
d=1 for all 8 types.** First differencing achieves clean stationarity for
every type (all ADF stats beyond -10, p < 0.00001). This is an expected,
real consequence of the phase-bug fix and recalibration, not a defect: the
corrected season is deeper (39% trough vs. the effective ~55-65% range the
buggy double-dip produced in practice) and holds for longer — the two
elevated half-widths (84 + 112 = 196 days) now cover more than half the
year — so within a 2-year (2-cycle) sample, the series' excursions away
from its mean are large and sustained enough that ADF can't reject a
unit root, even though the underlying process is still genuinely bounded
and mean-reverting by construction (periodic restocks, a fixed seasonal
target). This is a known ADF limitation with slow, long-period cycles and
short samples, not evidence the generator is now unstable — recorded here
because the methodology says to follow the data-driven test result, not
override it with that caveat.

ACF/PACF (40 lags) on the once-differenced series showed a **different
signature than before**: ACF cuts off sharply after lag ~4 for every type,
while PACF decays gradually across many lags — a classic MA signature (the
previous, buggy-data result showed the opposite: PACF cutting off after
lag 1, an AR(1) signature). This is the data-driven starting point for
Stage 3.

---

## 3. Parameter estimation (Stage 3) — re-run 2026-08-21

Candidates: SARIMAX(0,1,1), (0,1,4), and (1,1,1), exogenous regressor =
binary dengue-season flag (calendar months 6–10, matching
`DENGUE_SEASON_MONTHS` in `main.py`) — re-identified per Stage 2's new d=1,
MA-signature finding.

**Methodological note (not a new bug, an extension of the original
intercept fix's reasoning):** the original `trend='c'` requirement applied
to d=0 candidates, where an intercept anchors the baseline level. For d≥1
(every candidate here), a constant instead represents a linear *drift* in
the undifferenced series — and this generator has none by design (bounded,
mean-reverting, seasonal, no long-run trend). Adding one would be an
unjustified assumption, not a fix for a missing intercept, so
`_fit_candidate` now sets `trend='c'` only when `d=0` and `trend=None`
otherwise.

AIC **and** BIC both preferred **SARIMAX(0,1,4)** over the other two
candidates for **all 8 blood types, uniformly** — a cleaner, unanimous win
than the previous (1,0,1) result (which only had AIC preference recorded).
Dengue coefficient (best candidate):

| Type | Coef | SE | p-value | Significant @5%? | AIC | BIC |
|---|---|---|---|---|---|---|
| O+ | -5.88 | 8.28 | 0.47795 | No | 6254.00 | 6281.51 |
| O- | -8.81 | 6.16 | 0.15233 | No | 5867.22 | 5894.73 |
| A+ | -13.49 | 6.28 | 0.03175 | **Yes** | 5950.86 | 5978.37 |
| A- | -3.52 | 3.97 | 0.37620 | No | 5277.95 | 5305.46 |
| B+ | -2.00 | 3.87 | 0.60567 | No | 5497.84 | 5525.34 |
| B- | -5.41 | 2.37 | 0.02246 | **Yes** | 4978.05 | 5005.56 |
| AB+ | -2.15 | 2.37 | 0.36530 | No | 4437.40 | 4464.91 |
| AB- | -2.42 | 2.12 | 0.25410 | No | 4307.46 | 4334.97 |

All 8 coefficients are negative (correct direction). 2 of 8 clear 5%
significance — **A+ and B-** this time (the previous, buggy-data result had
A- and AB+ significant instead). Same qualitative story as before: a
consistent negative effect across every type, but only the types with a
favorable signal-to-noise ratio clear formal significance.

---

## 4. Diagnostic checking (Stage 4) — fails — re-run 2026-08-21

Ljung-Box test on SARIMAX(0,1,4)+dengue residuals, lags 10/20/30/40,
`model_df=4`:

| Type | p @10 | p @20 | p @30 | p @40 |
|---|---|---|---|---|
| O+ | <0.00001 | <0.00001 | 0.00002 | 0.00022 |
| O- | 0.00598 | 0.00609 | 0.02062 | 0.02951 |
| A+ | <0.00001 | <0.00001 | 0.00001 | <0.00001 |
| A- | <0.00001 | 0.00003 | 0.00049 | 0.00222 |
| B+ | <0.00001 | 0.00003 | 0.00029 | 0.00335 |
| B- | 0.00018 | 0.00306 | 0.01151 | 0.00420 |
| AB+ | 0.00001 | 0.00004 | 0.00022 | 0.00209 |
| AB- | <0.00001 | <0.00001 | 0.00001 | 0.00001 |

**Fails for every blood type at every lag, same as the pre-calibration
result.** The MA(4) term absorbs the short-range structure Stage 2 flagged,
but something periodic is still left in the residuals — consistent with the
generator's 5–9 day randomized restock interval, unchanged by this
recalibration (only the seasonal shape changed, not the restock model).

---

## 5. Back to identification — seasonal respecification (Stage 5) — re-run 2026-08-21

Seasonal ACF/PACF of the Stage 4 residuals at multiples of 7 (SARIMAX(0,1,4)+dengue):

| Type | lag 7 ACF | lag 7 PACF | lag 14 ACF | lag 14 PACF |
|---|---|---|---|---|
| O+ | 0.061 | 0.056 | 0.078 | 0.062 |
| O- | 0.077 | 0.075 | 0.062 | 0.041 |
| A+ | 0.051 | 0.038 | 0.003 | -0.024 |
| A- | 0.090 | 0.084 | 0.058 | 0.039 |
| B+ | 0.079 | 0.074 | 0.064 | 0.032 |
| B- | 0.015 | 0.009 | 0.066 | 0.056 |
| AB+ | 0.040 | 0.032 | 0.044 | 0.021 |
| AB- | 0.006 | 0.001 | 0.047 | 0.030 |

**Weaker than the pre-calibration result** (which showed 0.08–0.13 at lag 7,
uniformly exceeding the ~0.073 significance bound, for every type). Here
most types sit near or under the bound at lag 7 — expected, since the
richer MA(4) base already absorbs more of the short-range structure than
the old AR(1) base did, leaving less for a seasonal term to explain. Still
tried as a candidate per the same bounded-search methodology:

AIC preferred **SARIMAX(0,1,4)×(1,0,1,7)+dengue** over the non-seasonal
base and over (1,0,0,7) for all 8 types, uniformly (e.g. O+: 6254.00 →
6191.15):

| Type | AIC (0,1,4)×(1,0,1,7) | Coef | p-value | Ljung-Box (all 4 lags) |
|---|---|---|---|---|
| O+ | 6191.15 | -6.48 | 0.41710 | FAIL |
| O- | 5792.51 | -9.13 | 0.10792 | FAIL (2 of 4 lags now pass) |
| A+ | 5901.08 | -13.35 | 0.03361 | FAIL |
| A- | 5223.27 | -3.25 | 0.39728 | FAIL |
| B+ | 5439.31 | -2.00 | 0.59567 | FAIL |
| B- | 4934.59 | -5.41 | 0.02345 | FAIL |
| AB+ | 4391.36 | -2.02 | 0.37387 | FAIL |
| AB- | 4269.02 | -2.43 | 0.24340 | FAIL |

**The seasonal term still improves AIC for every blood type, and Ljung-Box
still fails for every blood type overall** — same conclusion as
pre-calibration, with one small nuance: O- now clears 2 of its 4 lags
(30, 40) rather than 0 of 4. Not a resolution, just a smaller gap.

### Bounded respecification (Stage 5b) — re-run 2026-08-21

Two more candidates, adapted from the original pair (higher-order seasonal
MA; a bumped-up non-seasonal alternative with no seasonal term):

- **SARIMAX(0,1,4)×(1,0,2,7)+dengue**: AIC improves further for every
  type; Ljung-Box still fails everywhere except a few incidental passes
  (O- clears 3 of 4 lags; B+ clears its 40-lag test only).
- **SARIMAX(1,1,4)+dengue** (added non-seasonal AR term, no seasonal
  term): worse AIC than the seasonal candidate for every type; Ljung-Box
  still fails everywhere, no incidental passes.

**Neither candidate cleared the diagnostic for any blood type** —
`bounded_respecification_synthetic_forecast_data.py` prints this directly:
`Any candidate cleared Ljung-Box for any blood type: False`. Same outcome
as the pre-calibration search. The identification loop was stopped here by
deliberate decision again, not because the search was exhausted to its limit.

### Known limitation (recorded explicitly, unchanged by recalibration)

**The unresolved Ljung-Box failure is tied to this synthetic generator's
randomized restock interval (5–9 days), not a defect in the SARIMAX
methodology itself, and not something the seasonal recalibration touched.**
No fixed-period ARMA/SARIMAX structure can fully absorb an irregular
renewal process. **This does not carry over to real historical data
automatically** — once real data is available, it needs its own fresh
identification → estimation → diagnostic-checking cycle from scratch.

---

## 6. Forecast cross-validation (Phase 4) — re-run 2026-08-21

**Method:** unchanged — rolling-origin (walk-forward) evaluation, 8 origins
spaced 20 days apart over the last ~160 days of the series, 14-day forecast
horizon, model refit fresh at each origin, compared against flat persistence
and seasonal-naive (lag-7 cycle) baselines.

**Mean absolute error, 14-day horizon, averaged over 8 origins —
SARIMAX(0,1,4)×(1,0,1,7)+dengue:**

| Type | SARIMAX MAE | Seasonal-naive MAE | Persistence MAE | vs seasonal-naive | vs persistence |
|---|---|---|---|---|---|
| O+ | 19.968 | 22.518 | 18.732 | +11.3% | -6.6% |
| O- | 14.743 | 16.884 | 19.920 | +12.7% | +26.0% |
| A+ | 15.442 | 19.062 | 15.670 | +19.0% | +1.5% |
| A- | 9.507 | 9.116 | 11.402 | -4.3% | +16.6% |
| B+ | 10.319 | 15.170 | 11.705 | +32.0% | +11.8% |
| B- | 8.378 | 9.500 | 10.821 | +11.8% | +22.6% |
| AB+ | 5.776 | 6.304 | 7.000 | +8.4% | +17.5% |
| AB- | 4.511 | 5.973 | 5.330 | +24.5% | +15.4% |

**Interpretation.** SARIMAX beats seasonal-naive for **7 of 8** blood types
(+8% to +32% lower MAE) — one fewer than the pre-calibration result (8 of
8), with **A- (-4.3%)** now the exception. Against plain persistence it
wins for **6 of 8** types (same count as before, but a different pair of
exceptions): **O+ (-6.6%)** stays the consistent weak point in both
versions, while **A+ (+1.5%)** is now only marginally positive. Notably,
**AB- flipped from a loss (-2.8%) to a solid win (+15.4%)** against
persistence — the type whose seasonal signal was most distorted by the old
double-dip artifact (its two troughs sat right at the edges of
`DENGUE_SEASON_MONTHS`) shows the clearest improvement now that the season
and the flag actually line up.

**Bottom line, unchanged in substance:** the Ljung-Box failure is real and
stayed unresolved after a reasonable, bounded search — but it hasn't
translated into broadly unreliable forecasts. SARIMAX(0,1,4)×(1,0,1,7)+dengue
is a modest, inconsistent-but-mostly-positive improvement over naive
baselines on this synthetic dataset. That is the model now wired into
`/forecast` as the explicitly-labeled synthetic stand-in (see `main.py`'s
`SYNTHETIC_MODEL_LABEL`), pending real historical data and its own fresh
validation cycle.

---

## Bugs found and fixed, consolidated

1. **Generator zero-stockout bug** (§1) — unstable random-walk model,
   fixed by switching to a mean-reverting seasonal-target design.
2. **Omitted-intercept bug** (§3, original) — AR(1) pulled toward
   near-unit-root to compensate for a missing baseline level, corrupting
   the dengue coefficient; fixed with `trend='c'` for d=0 candidates.
3. **Convergence bug** (§5, original) — default `maxiter=50` insufficient
   for seasonal candidates, producing one silently-wrong coefficient;
   fixed by checking `mle_retvals['converged']` explicitly and raising
   `maxiter`.
4. **Seasonal phase-inversion bug (§0, found 2026-08-21, the most
   consequential)** — `_seasonal_multiplier()`'s cosine formula had an
   inverted phase: it returned baseline exactly at the nominal peak date
   and the trough value at the season edges, producing a double-dip shape
   whose real lows (~May 15, ~Nov 15) fell outside `DENGUE_SEASON_MONTHS`
   (Jun–Oct) entirely — the exact window Stages 3–6 regressed against.
   Verified directly against the untouched original formula before
   fixing. This affected every downstream number in this document prior
   to 2026-08-21, and the then-live cached forecast
   (`fit_and_cache_synthetic_forecast.py`'s output). Fixed by removing an
   extraneous `(1 - frac_from_peak)` term. Combined with the real-data
   seasonal recalibration (§0), this changed Stage 2's finding from d=0 to
   d=1 for every blood type, which cascaded through every later stage's
   candidate orders — the entire pipeline (Stages 2–6 plus the live cache)
   was re-run end to end rather than patching individual numbers, given
   the shape changed qualitatively, not just in amplitude.

All four were caught by cross-checking results against independently-known
facts about the data or the code (the generator's own docstring and design,
Stage 2's PACF values, plain algebraic equivalence of two parameterizations,
direct unit-testing of the unmodified formula) rather than accepted at face
value — consistent with this project's standing rule against presenting
unverified output.

## Scripts, in pipeline order

`generate_synthetic_forecast_data.py` → `verify_synthetic_forecast_data.py`
→ `identify_synthetic_forecast_data.py` → `estimate_synthetic_forecast_models.py`
→ `diagnose_synthetic_forecast_models.py` → `reidentify_seasonal_synthetic_forecast_data.py`
→ `bounded_respecification_synthetic_forecast_data.py` → `crossvalidate_synthetic_forecast_model.py`
→ `fit_and_cache_synthetic_forecast.py` (writes the live cache `/forecast` actually reads)
