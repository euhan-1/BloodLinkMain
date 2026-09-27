-- Per-(facility, blood_type) SELECTED SARIMAX order, the output of the offline
-- grid search (see main.py:_run_order_selection_for_facility). A dedicated table
-- rather than new columns on facility_forecast_cache: a selection is one audited
-- decision per facility+type with its own lifecycle (re-run per historical
-- upload / server/select_orders.py), unrelated in shape to the 7 forecast-
-- checkpoint rows that table holds per type, and it must survive a
-- facility_forecast_cache DELETE (cache invalidation must never erase which
-- order to use next).
--
-- One row per (facility_id, blood_type) — the CURRENT selection only.
--
-- HOLD-OUT DESIGN (avoids post-selection inference: the order must not be
-- chosen using the same data its own diagnostics are then reported against).
-- The order is chosen using only the series through `trained_through_date`
-- (today minus 30 days) — the *_in_sample columns are Ljung-Box on THAT fit's
-- own residuals, exactly what the selection criterion was computed from. The
-- winning order is then refit on that same training window and used to
-- forecast the 30 held-out days through `evaluated_through_date` (today); the
-- *_holdout columns and the MAPE/RMSE columns are computed against actual
-- observations the selection never saw. holdout_baseline_mape/rmse is the same
-- 30 days scored by a naive last-training-value-repeated forecast, so the
-- selected order's holdout numbers can be read against a real baseline instead
-- of in isolation.
--
-- If no row exists for a (facility, blood_type), the live fitter falls back to
-- the fixed FACILITY_SARIMAX_ORDER and labels the forecast as a fallback,
-- never as a selected one.
DROP TABLE IF EXISTS facility_sarimax_order;  -- pre-hold-out-redesign shape; nothing else depends on it yet

CREATE TABLE facility_sarimax_order (
    facility_id bigint NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    blood_type text NOT NULL,
    p smallint NOT NULL,
    d smallint NOT NULL,
    q smallint NOT NULL,
    seasonal_p smallint NOT NULL,
    seasonal_d smallint NOT NULL,
    seasonal_q smallint NOT NULL,
    seasonal_s smallint NOT NULL,
    -- AIC of the TRAINING-only fit — the ranking criterion selection used.
    aic double precision NOT NULL,
    -- Ljung-Box p-values (df-corrected by this candidate's own K = p+q+seasonal_p+seasonal_q)
    -- at lags 14/21/28, computed on the TRAINING fit's own residuals — the
    -- values the selection criterion was actually computed from.
    lb_p_14_in_sample double precision NOT NULL,
    lb_p_21_in_sample double precision NOT NULL,
    lb_p_28_in_sample double precision NOT NULL,
    -- True iff ALL THREE in-sample lags above passed (p > 0.05) — the selection
    -- criterion. False means this row is the best-AIC candidate among those
    -- that converged on the training data, with NO candidate satisfying the
    -- independence criterion; the live fitter still uses it.
    criterion_satisfied boolean NOT NULL,
    n_candidates_converged smallint NOT NULL,
    -- Ljung-Box p-values at lags 14/21/28 on 30 ONE-STEP-AHEAD hold-out forecast
    -- errors (via fit.append(holdout, refit=False), NOT the static multi-step
    -- forecast errors MAPE/RMSE below use) — uncorrected (model_df=0): nothing
    -- was estimated on this data, so no parameter-count correction applies.
    -- NULL if the series was too short to hold out 30 days (see
    -- main.ORDER_SELECTION_HOLDOUT_DAYS), or if the append/test failed.
    -- Ljung-Box on the STATIC multi-step forecast errors would be invalid:
    -- h-step-ahead errors from one fixed origin are autocorrelated by
    -- construction even for a correctly specified model, and n=30 cannot
    -- support these lags regardless — that check is deliberately not stored.
    lb_p_14_holdout_onestep double precision,
    lb_p_21_holdout_onestep double precision,
    lb_p_28_holdout_onestep double precision,
    holdout_mape double precision,
    holdout_rmse double precision,
    -- Same 30 hold-out days, scored against a naive last-training-value-repeated
    -- forecast, so holdout_mape/rmse above can be read against a real baseline.
    holdout_baseline_mape double precision,
    holdout_baseline_rmse double precision,
    -- This winning candidate's OWN converged params from the grid search
    -- (_fit_candidate_order), in statsmodels' own params order for this exact
    -- (p,d,q)x(P,D,Q,s)+dengue spec. Passed as start_params to warm-start the
    -- production/live refit on the FULL series (main.py:_fit_sarimax_facility_forecast)
    -- instead of statsmodels' generic default start — see the module comment above
    -- SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD for the convergence failure (Northside
    -- O+, 2026-09) this exists to prevent: mle_retvals.converged=True from a default
    -- start landed on a materially worse, non-invertible optimum on the same data.
    -- Same row, same upsert as the order it belongs to, so a re-selection replaces
    -- both together and a stale vector can never attach to a different spec. NULL on
    -- a row selected before this column existed, until the next re-selection.
    start_params jsonb,
    -- That same winning candidate's AIC / training-window observation count — the
    -- baseline _sarimax_fit_is_sane compares the production/live fit's own AIC per
    -- observation against, so a fit that's technically "converged" but landed
    -- somewhere much worse than its own selection basin is still caught. NULL
    -- alongside start_params for a pre-migration row.
    selection_aic_per_obs double precision,
    -- The last TRAINING day (today minus ORDER_SELECTION_HOLDOUT_DAYS) — NOT a
    -- freshness gate (selection re-runs only when triggered, never
    -- automatically from the passage of a day, unlike
    -- facility_forecast_cache.trained_through_date).
    trained_through_date date NOT NULL,
    -- The last HOLD-OUT day (today, at the time this selection ran).
    evaluated_through_date date NOT NULL,
    selected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, blood_type)
);

COMMENT ON TABLE facility_sarimax_order IS
    'Per-facility, per-blood-type SARIMAX order chosen offline by grid search on TRAINING data only (main.py:_run_order_selection_for_facility / server/select_orders.py), with diagnostics reported separately on a 30-day hold-out the selection never saw. Absent = the live fitter falls back to the fixed FACILITY_SARIMAX_ORDER.';
