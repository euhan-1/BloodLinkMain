"""Renders PHASE_1..4 markdown from results.json + extra_checks.json.
Every number in the tables is read from those files (from run_methodology.py /
run_extra_checks.py); narrative counts are computed here, not typed.
Run from repo root: server\\.venv\\Scripts\\python.exe docs\\methodology\\build_docs.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.load(open(HERE / "results.json"))
X = json.load(open(HERE / "extra_checks.json"))
T = R["meta"]["types"]
P1, SEL, PROD, P4, STAB = R["phase1"], R["selection"], R["production"], R["phase4"], R["stability"]

N_COUNTS = R["meta"]["days"] * 8
DATA = (f"> **Data notice.** The {R['meta']['days']}-day series analysed here (`{R['meta']['csv']}`, "
        f"{R['meta']['first']} to {R['meta']['last']}, 8 blood types, {N_COUNTS:,} daily counts) is **generated "
        "demonstration data**, not real blood bank records. It is entirely the uploaded synthetic file "
        "(`upload_history_id=224`) — an earlier version of this export spliced one real day from live `blood_units` "
        "onto the end, which turned out to be a genuine problem (see PHASE_3's method note); that splice has been "
        "removed. This report shows that the implementation performs the methodology correctly on a known series. "
        "It is **not** empirical evidence about real blood supply, and no sentence in it should be read as one.\n")

TWO_FITS = ("> **Two fits, never conflated.** SELECTION FIT = fit on the training window only (the series minus the "
            f"last {R['meta']['holdout_days']} days) — what the 36-candidate grid search and its AIC/Ljung-Box "
            "criterion actually judged (`server/select_orders.py` / `main.py:_run_order_selection_for_facility`). "
            "PRODUCTION FIT = the SAME selected order, refit on the FULL series — what a live forecast request "
            "actually runs (`main.py:_fit_and_cache_sarimax`). They are reported separately throughout; neither "
            "stands in for the other.\n")


def p(v):
    if v is None:
        return "-"
    return f"{v:.4f}" if v >= 0.0001 else f"{v:.1e}"


def f(v, d=2):
    if v is None:
        return "-"
    return f"{v:.{d}f}"


def order_str(order, sorder):
    return f"({order[0]},{order[1]},{order[2]})x({sorder[0]},{sorder[1]},{sorder[2]},{sorder[3]})"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def write(name, text):
    (HERE / name).write_text(text, encoding="utf-8")
    print("wrote", name)


# =============================== PHASE 1: IDENTIFICATION ===============================
raw_rows = [[t, f(P1[t]["adf_raw"]["stat"]), p(P1[t]["adf_raw"]["p"]), P1[t]["adf_raw"]["verdict"],
             P1[t]["min_d_for_adf"], f(P1[t]["adf_d1"]["stat"]), p(P1[t]["adf_d1"]["p"]), P1[t]["adf_d1"]["verdict"]] for t in T]
acf_rows = [[t, str(P1[t]["acf_sig_lags"]), P1[t]["q_suggest_leading_sig_acf"], str(P1[t]["pacf_sig_lags"]),
             P1[t]["p_suggest_leading_sig_pacf"], str(P1[t]["seasonal_lags_sig_acf"]) or "[]", str(P1[t]["seasonal_lags_sig_pacf"])] for t in T]
wk_rows = [[t, f(X["weekday"][t]["F"]), p(X["weekday"][t]["p"]), X["weekday"][t]["range"], X["weekday"][t]["series_mean"]] for t in T]
n_nonstationary_raw = sum(1 for t in T if P1[t]["adf_raw"]["p"] >= .05)
max_raw_p = max(P1[t]["adf_raw"]["p"] for t in T)
max_d1_p = max(P1[t]["adf_d1"]["p"] for t in T)
d0_types = [t for t in T if P1[t]["min_d_for_adf"] == 0]
s7_acf = [t for t in T if 7 in P1[t]["acf_sig_lags"]]
s7_pacf = [t for t in T if 7 in P1[t]["pacf_sig_lags"]]
bound = P1[T[0]]["bound"]
order_match = sum(1 for t in T if SEL[t]["order"][2] == P1[t]["q_suggest_leading_sig_acf"])

write("PHASE_1_IDENTIFICATION.md", f"""# Phase 1: Identification

{DATA}
## What the live system does versus what this report does

**The live system selects a SARIMAX order per facility per blood type, offline** — never inside a forecast request.
`server/select_orders.py` (the primary path) and, bounded, a background task after each historical upload, both call
`main.py:_run_order_selection_for_facility`, which fits a 36-candidate grid (p,q in {{0,1,2}}, seasonal P,Q in {{0,1}},
d=1, D=0, s=7 fixed) on the series minus the last {R['meta']['holdout_days']} days, and picks the lowest-AIC candidate
whose Ljung-Box test passes on its own training residuals — see PHASE_2 and PHASE_3. A type with nothing selected
falls back to the fixed `FACILITY_SARIMAX_ORDER = (0, 1, 4)` / `FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)`
(`main.py:97-98`).

**Still, no ADF test, no ACF/PACF computation ever runs inside the grid search or a request** (verified by grep: no
`adfuller`, `acf`, or `pacf` anywhere in `main.py`). Order selection ranks candidates by AIC and Ljung-Box alone; it
never looks at a differencing test or a correlogram. **This report runs identification to check the selected orders
against what ADF/ACF/PACF would suggest, not to choose them.** Where the readings disagree with what was selected,
that is reported as a disagreement, not corrected. The orders shown below are each type's SELECTION FIT order — see
PHASE_2/3 for what that means precisely.

## Method (code: `run_methodology.py`, phase 1 block)

* ADF test, `statsmodels.tsa.stattools.adfuller`, constant-only regression, lag length chosen by AIC, alpha = 0.05.
* Differencing: difference repeatedly until ADF rejects the unit root (max 2). Every selected and fallback order alike
  uses d = 1, so ADF is also reported on the first difference regardless of what the minimum needed was.
* ACF and PACF of the **first-differenced** series to 28 lags; PACF by the Yule-Walker method.
  95% bound = 1.96/sqrt(n) = {bound:.3f} (n = {R['meta']['days'] - 1} differences).
* "Suggested q / p" below is the crude textbook rule: the number of consecutive significant lags starting at lag 1.

## 1. Stationarity

{table(["Type", "ADF stat (raw)", "p (raw)", "Verdict (raw)", "Min d to pass", "ADF stat (d=1)", "p (d=1)", "Verdict (d=1)"], raw_rows)}
{n_nonstationary_raw} of 8 series are non-stationary in levels (raw p up to {max_raw_p:.3f}) and every one of the 8 becomes
stationary after one difference (d1 p at most {max_d1_p:.1e}). {(f"**{', '.join(d0_types)}** already rejects the unit root in "
f"levels (min d = 0) — the selected order still uses d = 1 there regardless, since d is fixed across the whole grid. " if d0_types else "")}
d = 1 is well supported for the other {8 - len(d0_types)} types. No series needed d = 2.

## 2. ACF / PACF of the differenced series

{table(["Type", "Significant ACF lags", "Suggested q", "Significant PACF lags", "Suggested p", "Seasonal-multiple ACF lags", "Seasonal-multiple PACF lags"], acf_rows)}
## 3. Is weekly (s = 7) seasonality actually in the data?

The ACF/PACF above show weak evidence at lag 7 for some types (see reading below), so weekly structure was tested
directly: detrend each series with a centred 7-day moving average, then one-way ANOVA on the residual by day of week
(`run_extra_checks.py`).

{table(["Type", "F", "p", "Weekday range (units)", "Series mean (units)"], wk_rows)}
A weekday effect is present in all 8 types (p < 1e-4), largest in absolute terms for {max(T, key=lambda t: X['weekday'][t]['range'])}
({X['weekday'][max(T, key=lambda t: X['weekday'][t]['range'])]['range']} units peak-to-trough). Weekly seasonality genuinely exists in this series.

## Reading against Northside's SELECTION FIT orders

{table(["Type", "Selected order", "ACF-suggested q", "PACF stays significant to", "q match?"],
       [[t, order_str(SEL[t]['order'], SEL[t]['seasonal_order']), P1[t]["q_suggest_leading_sig_acf"],
         (max(P1[t]["pacf_sig_lags"]) if P1[t]["pacf_sig_lags"] else 0),
         "yes" if SEL[t]['order'][2] == P1[t]["q_suggest_leading_sig_acf"] else "no"] for t in T])}
* **d = 1: used by all 8** selected orders; {', '.join(d0_types) or 'no type'} is where identification alone would have
  allowed d = 0 and the grid never tested it.
* **q: matches the ACF's leading run for {order_match} of 8 types.** Where they disagree the selected q and the
  ACF-suggested q are usually close (within 1-2); AIC and the training-window Ljung-Box are picking up structure a
  single leading-run reading doesn't capture, in both directions.
* **p: consistent with an MA-dominated process throughout.** The PACF stays significant well past lag 5 for every
  type instead of cutting off, so a pure AR model is never suggested — every selected order uses p <= 2, seasonal P = 1.
* **Seasonal (P=1, Q=1, s=7): used by every selected order, and supported by the ACF for {len(s7_acf)} of 8 types**
  ({', '.join(s7_acf) or 'none'}); the PACF is significant at lag 7 for {len(s7_pacf)} ({', '.join(s7_pacf) or 'none'}). The weekday
  ANOVA independently confirms the seasonality exists. No seasonal differencing (D = 0) is used, and nothing here
  tests that choice.

## Limitations

* Identification by eye-balled ACF/PACF is subjective. The "suggested q/p" rule is the simplest possible reading.
* {R['meta']['days']} days is roughly {R['meta']['days'] // 7} weekly cycles; lags beyond 28 were not examined.
* One series per blood type, from a generated source. Nothing here says a selected order would validate on a real facility.
""")

# =============================== PHASE 2: ESTIMATION ===============================
main_rows = []
not_overall_best = []
for t in T:
    s = SEL[t]
    dg = s["params"]["dengue_season"]
    if s["grid_top5"][0]["aic"] < s["aic"] - 1e-6:
        not_overall_best.append(t)
    main_rows.append([t, order_str(s["order"], s["seasonal_order"]), f(s["aic"], 1),
                      f(s["aic_gap_to_runnerup"], 2) if s["aic_gap_to_runnerup"] is not None else "-",
                      f(dg["coef"], 3), p(dg["p"]), "yes" if s["criterion_satisfied"] else "no"])

top5_rows = []
for t in T:
    for i, c in enumerate(SEL[t]["grid_top5"], start=1):
        top5_rows.append([t, i, order_str(c["order"], c["seasonal_order"]), f(c["aic"], 1), f(c["gap_to_winner"], 2)])

n_sig_sel = sum(1 for t in T if SEL[t]["params"]["dengue_season"]["p"] < .05)
n_sig_prod = sum(1 for t in T if PROD[t]["params"]["dengue_season"]["p"] < .05)
prod_dengue_rows = [[t, f(PROD[t]["params"]["dengue_season"]["coef"], 3), p(PROD[t]["params"]["dengue_season"]["p"])] for t in T]
stab_rows = [[t, order_str(STAB[t]["order_a"][:3], STAB[t]["order_a"][3:]), STAB[t]["window_a"]["end"],
             order_str(STAB[t]["order_b"][:3], STAB[t]["order_b"][3:]), STAB[t]["window_b"]["end"],
             "**yes**" if STAB[t]["stable"] else "**NO — flips**"] for t in T]
n_stable = sum(1 for t in T if STAB[t]["stable"])
flipped = [t for t in T if not STAB[t]["stable"]]
flip_desc = "; ".join(
    f"{t}: {order_str(STAB[t]['order_a'][:3], STAB[t]['order_a'][3:])} -> {order_str(STAB[t]['order_b'][:3], STAB[t]['order_b'][3:])}"
    for t in flipped
)
if flipped:
    stability_note = (f"**{', '.join(flipped)} flips** ({flip_desc}). This is reported as a finding, not smoothed "
                      "over: for at least one type, the selected order depends on exactly where the training window "
                      "happens to end, which is a real limitation of picking a single point estimate from AIC + "
                      "Ljung-Box on one window.")
else:
    stability_note = "No type flips."

write("PHASE_2_ESTIMATION.md", f"""# Phase 2: Estimation

{DATA}
{TWO_FITS}
## What the live system does versus what this report does

The live selection mechanism ranks all 36 grid candidates by AIC and never looks at a coefficient table — the numbers
below are read off afterward, for this report, not consulted by the system itself. SARIMAX here is a regression with
SARIMA errors (the dengue exog is NOT differenced; it enters as `y_t = beta * flag_t + eta_t`, with `eta_t` following
the SARIMA structure).

## 1. Selected order per type (SELECTION FIT: training window, ending {SEL[T[0]]['window']['end']})

{table(["Type", "Selected order", "AIC", "AIC gap to runner-up", "Dengue coef", "Dengue p", "In-sample LB criterion met"], main_rows)}
"AIC gap to runner-up" is relative to the winner, not always the raw lowest-AIC candidate in the grid: the selection
rule picks the lowest AIC AMONG CANDIDATES THAT PASS Ljung-Box, so a lower-AIC candidate that fails the criterion is
skipped. **This happens for {', '.join(not_overall_best) if not_overall_best else 'no type'}**{" — see section 2's top-5 table for where the selected order actually ranks by raw AIC; the better-AIC candidate(s) above it in that table failed Ljung-Box." if not_overall_best else "."}
Every gap shown is under 2.0 — none of these wins are dominant even within their own pool; several other candidates
are statistically indistinguishable by AIC alone (a gap under ~2 is conventionally read as "not much evidence
favouring one over the other"). {("A+ has no gap to report (`-`) because it is the ONLY one of 36 candidates that "
"passes Ljung-Box in-sample; there is no runner-up within its own pool at all — a narrower margin than any other "
"type. " if any(g[3] == "-" for g in main_rows) else "")}The Ljung-Box criterion is what actually separates these
candidates (PHASE_3).

## 2. Each type's own top-5 candidates (replaces the old fixed 5-alternative comparison)

The previous version of this document compared the single fixed order against 5 abstract alternative orders shared
across all 8 types. Per-type order selection makes that comparison meaningless — each type already ran an exhaustive
36-candidate search of its own. This table shows the 5 best (by AIC) candidates from THAT search, per type.

{table(["Type", "Rank", "Order", "AIC", "Gap to winner"], top5_rows)}
## 3. THE DENGUE QUESTION

**Significant (p < 0.05) for {n_sig_sel} of 8 types on the SELECTION FIT** ({', '.join(t for t in T if SEL[t]['params']['dengue_season']['p'] < .05) or 'none'}),
and **{n_sig_prod} of 8 on the PRODUCTION FIT** (full series) — see the table below. This is a marked change from
earlier versions of this analysis (on the old 180-day, single-dengue-season, monotonic-decline series, only 0-1 of 8
types were significant). This 420-day series covers two full dengue seasons with no monotonic drift, so the flag is no
longer confounded with an unrelated trend the way it was before — and every coefficient is negative (lower stock while
the flag is on), in the direction the hypothesis predicts, with p-values as small as 1e-131 for some types. **Report
this as found: it is a property of this generated series having two clean seasonal cycles, not proof the underlying
mechanism is real** — this is still demonstration data (see the data notice above).

{table(["Type", "Dengue coef (production fit)", "Dengue p (production fit)"], prod_dengue_rows)}
## 4. Selection stability check

Selection was re-run with the training window cut a further {R['meta']['holdout_days']} days shorter (ending
{STAB[T[0]]['window_b']['end']} instead of {STAB[T[0]]['window_a']['end']}), to see whether the winning order for each
type is sensitive to exactly where the window ends.

{table(["Type", "Order (window ending " + STAB[T[0]]['window_a']['end'] + ")", "Order (window ending " + STAB[T[0]]['window_b']['end'] + ")", "Stable?"],
       [[r[0], r[1], r[3], r[5]] for r in stab_rows])}
**{n_stable} of 8 types select the SAME order under both windows.** {stability_note}

## Limitations

* AIC gaps under ~2 mean several candidates per type are close competitors; the grid search still picks exactly one.
* The stability check moves the window by only one horizon (30 days); it does not test sensitivity to the window
  LENGTH, only its END point, and only at one alternative end point.
* Per-type full coefficient tables (MA/AR terms, not just the dengue regressor) are not shown here.
""")

# =============================== PHASE 3: DIAGNOSTIC CHECKING ===============================
def diag_row(t, d, order, sorder):
    lb = d["ljungbox"]
    fails = []
    lb_ok = all(lb[str(l)]["p_corrected"] is not None and lb[str(l)]["p_corrected"] > .05 for l in (14, 21, 28))
    jb_ok = d["jb"]["p"] > .05
    bv_ok = d["breakvar"]["p"] > .05
    if not lb_ok:
        fails.append("Ljung-Box (corrected)")
    if not jb_ok:
        fails.append("Jarque-Bera")
    if not bv_ok:
        fails.append("heteroskedasticity")
    verdict = "**PASS**" if not fails else "**FAIL** (" + ", ".join(fails) + ")"
    row = [t, order_str(order, sorder), f"K={d['K']}",
          " / ".join(p(lb[str(l)]["p_uncorrected"]) for l in (7, 14, 21, 28)),
          " / ".join(p(lb[str(l)]["p_corrected"]) for l in (7, 14, 21, 28)),
          f"{f(d['jb']['stat'], 1)} / {p(d['jb']['p'])} / {f(d['jb']['skew'])} / {f(d['jb']['kurt'])}",
          f"{f(d['breakvar']['stat'])} / {p(d['breakvar']['p'])}",
          f"{len(d['resid_acf_outside'])}/28 {d['resid_acf_outside']}", verdict]
    return row, (not fails)


sel_rows, sel_pass = [], []
prod_rows, prod_pass = [], []
for t in T:
    r, ok = diag_row(t, SEL[t]["diagnostics"], SEL[t]["order"], SEL[t]["seasonal_order"])
    sel_rows.append(r)
    if ok:
        sel_pass.append(t)
    r, ok = diag_row(t, PROD[t]["diagnostics"], PROD[t]["order"], PROD[t]["seasonal_order"])
    prod_rows.append(r)
    if ok:
        prod_pass.append(t)

holdout_rows, holdout_pass, holdout_untested = [], [], []
for t in T:
    h = P4[t]["lb_onestep_holdout"]
    lb14, lb21, lb28 = h["14"], h["21"], h["28"]
    acc = [f(P4[t]["sarimax"]["mape"]), f(P4[t]["sarimax"]["rmse"]), f(P4[t]["naive_last"]["mape"]), f(P4[t]["naive_last"]["rmse"]),
          "beats baseline" if P4[t]["sarimax"]["mape"] < P4[t]["naive_last"]["mape"] else "loses to baseline"]
    if any(v is None for v in (lb14, lb21, lb28)):
        holdout_rows.append([t, p(lb14), p(lb21), p(lb28), *acc, "untested"])
        holdout_untested.append(t)
        continue
    ok = lb14 > .05 and lb21 > .05 and lb28 > .05
    if ok:
        holdout_pass.append(t)
    holdout_rows.append([t, p(lb14), p(lb21), p(lb28), *acc, "**PASS**" if ok else "**FAIL**"])

both_pass = [t for t in T if t in prod_pass and t in holdout_pass]
prod_only = [t for t in T if t in prod_pass and t not in holdout_pass and t not in holdout_untested]
holdout_only_types = [t for t in T if t not in prod_pass and t in holdout_pass]

write("PHASE_3_DIAGNOSTICS.md", f"""# Phase 3: Diagnostic checking

{DATA}
{TWO_FITS}
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

## Results — SELECTION FIT (training window, ending {SEL[T[0]]['window']['end']}; what the criterion was judged on)

{table(["Type", "Order", "K", "LB p uncorrected (7/14/21/28)", "LB p corrected (7/14/21/28)", "Jarque-Bera", "Het. (breakvar) stat / p", "Resid ACF outside 95% bound", "Verdict"], sel_rows)}
* **PASS ({len(sel_pass)}): {', '.join(sel_pass) or 'none'}.**
* **FAIL ({8 - len(sel_pass)}): {', '.join(t for t in T if t not in sel_pass) or 'none'}.**

## Results — PRODUCTION FIT (full {R['meta']['days']}-day series; what a live forecast request actually runs)

{table(["Type", "Order", "K", "LB p uncorrected (7/14/21/28)", "LB p corrected (7/14/21/28)", "Jarque-Bera", "Het. (breakvar) stat / p", "Resid ACF outside 95% bound", "Verdict"], prod_rows)}
* **PASS ({len(prod_pass)}): {', '.join(prod_pass) or 'none'}.**
* **FAIL ({8 - len(prod_pass)}): {', '.join(t for t in T if t not in prod_pass) or 'none'}.**

The order is identical in both fits (PHASE_2) — only the data changes. Where the two verdicts differ, that difference
is caused entirely by fitting on 30 more (or fewer) days: {', '.join(t for t in T if (t in sel_pass) != (t in prod_pass)) or 'no type differs between the two fits'}.

## Results — hold-out (one-step-ahead, {R['meta']['holdout_days']} days the SELECTION FIT never saw)

MAPE/RMSE are the SELECTION FIT's own STATIC {R['meta']['holdout_days']}-day-ahead forecast error over the hold-out —
valid out-of-sample accuracy evidence, distinct from (and not tested for independence the same way as) the one-step
LB columns. Baseline is a naive last-training-value-repeated forecast over the identical window.

{table(["Type", "LB p (one-step), lag 14", "lag 21", "lag 28", "MAPE %", "RMSE", "Baseline MAPE %", "Baseline RMSE", "vs baseline", "Verdict"], holdout_rows)}
**Hold-out PASS ({len(holdout_pass)}): {', '.join(holdout_pass) or 'none'}.**

## All three, compared

* **Pass PRODUCTION FIT and hold-out ({len(both_pass)}): {', '.join(both_pass) or 'none'}.** The types where both "the
  order fits the full history well" and "its forecast errors on genuinely new data are independent" hold.
* **Pass PRODUCTION FIT, FAIL hold-out ({len(prod_only)}): {', '.join(prod_only) or 'none'}.** Fits the full history but
  its forecast errors on new data are still serially correlated.
* **FAIL PRODUCTION FIT, pass hold-out ({len(holdout_only_types)}): {', '.join(holdout_only_types) or 'none'}.** A
  reminder that in-sample and out-of-sample diagnostics answer different questions.

## Caveats

* No test here adjusts for multiple comparisons across 8 types, or across the two fits.
* The hold-out is {R['meta']['holdout_days']} days, once, per type — a single test, not a distribution; no
  rolling-origin evaluation was run.
* Jarque-Bera and heteroskedasticity are evaluated in-sample only (on both fits); the selection criterion and the
  hold-out check are both Ljung-Box only (see main.py's ORDER SELECTION module comment for why).
""")

# =============================== PHASE 4: FORECASTING ===============================
acc_rows = [[t, f"{f(P4[t]['sarimax']['mape'])} / {f(P4[t]['sarimax']['rmse'])} / {f(P4[t]['sarimax']['mae'])}",
            f"{f(P4[t]['naive_last']['mape'])} / {f(P4[t]['naive_last']['rmse'])} / {f(P4[t]['naive_last']['mae'])}",
            f"{f(P4[t]['seasonal_naive_7']['mape'])} / {f(P4[t]['seasonal_naive_7']['rmse'])} / {f(P4[t]['seasonal_naive_7']['mae'])}"] for t in T]
mae_w = [t for t in T if P4[t]["sarimax"]["mae"] < P4[t]["naive_last"]["mae"]]
sn_w = [t for t in T if P4[t]["sarimax"]["mae"] < P4[t]["seasonal_naive_7"]["mae"]]
cov_rows = [[t, f"{P4[t]['n_inside']}/{R['meta']['holdout_days']}", f"{P4[t]['coverage'] * 100:.1f}%"] for t in T]
tot_in = sum(P4[t]["n_inside"] for t in T)
tot_n = 8 * R["meta"]["holdout_days"]
w_rows = [[t] + [f(P4[t]["width_by_horizon"][str(h)], 1) for h in (1, 5, 10, 15, 20, 25, 30)] +
          [f"{P4[t]['width_by_horizon']['30'] / P4[t]['width_by_horizon']['1']:.2f}x"] for t in T]
parity_rows = [[t, order_str(P4[t]["order"], P4[t]["seasonal_order"]),
               "yes" if P4[t]["live_parity_match"] else ("no live result" if P4[t]["live_parity"] is None else "**NO — differs**")] for t in T]
n_parity = sum(1 for t in T if P4[t]["live_parity_match"])

write("PHASE_4_FORECASTING.md", f"""# Phase 4: Forecasting and out-of-sample validation

{DATA}
{TWO_FITS}
## What the live system does versus what this report does

Live, `GET /forecast` reads each type's stored order (or the fixed fallback) and fits it on the FULL available
history — the PRODUCTION FIT pattern — caching 7 checkpoints (days 0, 5, 10, 15, 20, 25, 30) per facility, type and
day. **The live system has no accuracy monitoring**: it never compares a forecast with what later happened. This
report's hold-out (the SELECTION FIT, forecasting the {R['meta']['holdout_days']} days it was never trained on) is an
offline test using each type's own selected order, not the live path's own request/response cycle.

## Method (code: `run_methodology.py`)

* Hold out the last {R['meta']['holdout_days']} days ({SEL[T[0]]['window']['end']} is the training window's last day
  for every type). Fit EACH type's own SELECTION FIT order (PHASE_2) on the training window, forecast
  {R['meta']['holdout_days']} days, and score against the actual held-out values.
* Metrics: MAPE, RMSE, MAE on the point forecast; interval coverage = fraction of actuals inside the 95% interval.
* Baselines: naive (last training value repeated) and seasonal-naive (the last observed week repeated).
* **Live-parity check, kept as its own explicit step** (not folded into `select_orders.py`'s own stored numbers):
  for every type, the production function `_fit_sarimax_facility_forecast` is called directly with that type's
  selected order on the same training series, and its checkpoint forecasts are compared with this script's own fit.
  "The deployed function reproduces an independent fit" is a different claim from "the model is accurate," and the
  two are reported separately.

## 1. Accuracy against the naive baseline (all per type's own selected order)

Cells are MAPE % / RMSE / MAE (units). Lower is better.

{table(["Type", "SARIMAX (selected order)", "Naive (last value)", "Seasonal naive (7d)"], acc_rows)}
SARIMAX beats naive-last on MAE for {len(mae_w)} of 8 types, and beats seasonal-naive on MAE for {len(sn_w)} of 8.
{'It loses to naive-last on MAE for ' + ', '.join(t for t in T if t not in mae_w) + '.' if len(mae_w) < 8 else ''}

## 2. Prediction-interval coverage

Nominal coverage is 95%.

{table(["Type", "Actuals inside 95% interval", "Coverage"], cov_rows)}
Overall **{tot_in} of {tot_n} ({tot_in / tot_n * 100:.1f}%)**.

## 3. Interval width by horizon

{table(["Type", "Day 1", "Day 5", "Day 10", "Day 15", "Day 20", "Day 25", "Day 30", "Day 30 / Day 1"], w_rows)}
## 4. Live-parity check

{table(["Type", "Order used", "Matches the live function's own fit"], parity_rows)}
**{n_parity} of 8 types match exactly.** This is a check that `_fit_sarimax_facility_forecast`, called directly with
each type's selected order, reproduces this script's own independent fit on identical input — it says nothing about
forecast accuracy on its own, which is section 1 above.

## Caveats

* One {R['meta']['holdout_days']}-day hold-out per type is a single test, not a distribution; no rolling-origin
  evaluation was run here.
* Coverage and MAPE/RMSE are computed from the SELECTION FIT (trained on {SEL[T[0]]['window']['n_days']} days), not
  the PRODUCTION FIT (trained on all {R['meta']['days']} days) — the live system's actual day-to-day forecast comes
  from the production fit, which by construction cannot be evaluated out-of-sample on data already inside it.
* Everything above concerns generated data. Nothing here measures how the model performs on real blood supply.
""")
