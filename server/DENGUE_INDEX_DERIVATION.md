# Graded dengue-season index — derivation and verification record

Investigated as a replacement for `dengue_season_index()`'s binary
June-October flag (`main.py`). **Not adopted** — kept here, with its source
data and derivation script, so the investigation is reproducible and the
next attempt (if any) doesn't repeat the same dead ends.

## Source data

`batangas_dengue_2016_2021_real.csv` — real DOH Epidemiology Bureau weekly
dengue case (and death) counts for Batangas province, 2016-01-10 through
2021-01-10. Supplied directly by the project owner, not fetched or
constructed by this derivation.

## Step 1 — a verbal spec produced values that didn't match this repo's own prior precedent

The index was first specified as "derived from 2016-2019, excluding 2020"
with 12 concrete monthly values already given (Jan 0.27 ... Sep 1.00 ...
Dec 0.29). Running `derive_dengue_season_index.py` against the real CSV
confirms those values are exactly what pooling **2016-2019** (2019
included) produces:

```
   1: 0.27   2: 0.23   3: 0.16   4: 0.08   5: 0.10   6: 0.18
   7: 0.44   8: 0.84   9: 1.00  10: 0.58  11: 0.44  12: 0.29
```

But `SYNTHETIC_SARIMAX_VALIDATION.md`'s existing "Seasonal calibration"
section (written earlier, calibrating the *synthetic* generator's dengue
shape, not this live exog) already excludes 2019 as a **declared national
dengue epidemic year** and calibrates on 2016-2018 only. The raw data backs
that up directly:

| year | peak week | total cases | weeks on file |
|---|---|---|---|
| 2016 | 240 | 5,010 | 51 |
| 2017 | 191 | 4,461 | 53 |
| 2018 | 209 | 4,961 | 52 |
| **2019** | **907** | **12,742** | 51 |
| 2020 | 148 | 1,271 | 52 |
| 2021 | 11 | 20 | 2 |

2019's peak week is ~4x every other typical year's, and its total is ~2.6x
the next-highest year — consistent with "epidemic," not "typical season."
Pooling it in measurably shifts the climatology (every month except
September moves by ≥0.05):

**2016-2018 only** (peak month = 1.00):
```
   1: 0.43   2: 0.31   3: 0.20   4: 0.12   5: 0.14   6: 0.26
   7: 0.63   8: 0.96   9: 1.00  10: 0.77  11: 0.64  12: 0.47
```

**2016-2019** (matches the originally-given values exactly):
```
   1: 0.27   2: 0.23   3: 0.16   4: 0.08   5: 0.10   6: 0.18
   7: 0.44   8: 0.84   9: 1.00  10: 0.58  11: 0.44  12: 0.29
```

The 2016-2018 window is the one consistent with this project's own prior,
already-documented reasoning for excluding epidemic years. This doc doesn't
resolve which one is "correct" for a defense narrative — that's a judgment
call — but flags that the two candidates are meaningfully different shapes,
not a rounding difference.

## Step 2 — both candidates fail live significance verification on the only real-history facility

Per the explicit stop condition this investigation was gated on ("if the
coefficient loses significance or fits start failing, stop"): refit
`FACILITY_SARIMAX_ORDER`/`FACILITY_SARIMAX_SEASONAL_ORDER` against facility
158's real history (152 days, Apr 24-Sep 23, all 8 blood types), swapping
only the exog column.

| | old binary flag | graded, 2016-2019 pooled | graded, 2016-2018 only |
|---|---|---|---|
| Significant (p<0.05) | 7/8 types | 0/8 types | 0/8 types |
| p-value range | mostly p<0.001 | 0.40 – 0.86 | 0.10 – 0.97 |
| AIC vs. the binary flag | — | worse on all 8 | worse on all 8 |

Both graded candidates fail identically, regardless of which year window
produced them. That's evidence the blocker isn't the specific shape — it's
that facility 158's real history is a single partial season (Apr-Sep) with
one on/off transition of the binary flag, which a short single-season sample
can fit as a simple level-shift dummy without it reflecting genuine
repeatable seasonality. A graded index that varies continuously across that
same window no longer lines up with that particular confound, so it scores
worse on this one dataset — that doesn't mean the real seasonal shape above
is wrong, only that this facility's data can't confirm it either way.

## Status

Not shipped. `dengue_season_index()` in `main.py` remains the original
binary June-October flag, with a note recording this attempt. Revisit only
against a facility with real multi-season history (or a proper synthetic/
cross-year validation), and re-run the same significance check, before
trying again — and settle the 2016-2018 vs. 2016-2019 question first, since
they're genuinely different candidates.
