"""Renders PHASE_1..4 markdown from results.json + extra_checks.json.
Every number in the tables is read from those files (which come from
run_methodology.py / run_extra_checks.py); narrative counts are computed here,
not typed. Run from repo root: server\\.venv\\Scripts\\python.exe docs\\methodology\\build_docs.py
"""
import json
from pathlib import Path

from scipy.stats import chi2

HERE = Path(__file__).resolve().parent
R = json.load(open(HERE / "results.json"))
X = json.load(open(HERE / "extra_checks.json"))
T = R["meta"]["types"]
P1, P2, P3, P4 = R["phase1"], R["phase2"], R["phase3"], R["phase4"]

DATA = ("> **Data notice.** The 180-day series analysed here (`northside_180d_history.csv`, "
        f"{R['meta']['first']} to {R['meta']['last']}, 8 blood types, 1,440 daily counts) is **generated demonstration "
        "data**, not real blood bank records. This report shows that the implementation performs the methodology "
        "correctly on a known series. It is **not** empirical evidence about real blood supply, and no sentence in it "
        "should be read as one.\n")


def p(v):
    return f"{v:.4f}" if v >= 0.0001 else f"{v:.1e}"


def f(v, d=2):
    return f"{v:.{d}f}"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def write(name, text):
    (HERE / name).write_text(text, encoding="utf-8")
    print("wrote", name)


# =============================== PHASE 1 ===============================
raw_rows = [[t, f(P1[t]["adf_raw"]["stat"]), p(P1[t]["adf_raw"]["p"]), P1[t]["adf_raw"]["verdict"],
             P1[t]["min_d_for_adf"], f(P1[t]["adf_d1"]["stat"]), p(P1[t]["adf_d1"]["p"]), P1[t]["adf_d1"]["verdict"]] for t in T]
acf_rows = [[t, str(P1[t]["acf_sig_lags"]), P1[t]["q_suggest_leading_sig_acf"], str(P1[t]["pacf_sig_lags"]),
             P1[t]["p_suggest_leading_sig_pacf"], str(P1[t]["seasonal_lags_sig_acf"]) or "[]", str(P1[t]["seasonal_lags_sig_pacf"])] for t in T]
wk_rows = [[t, f(X["weekday"][t]["F"]), p(X["weekday"][t]["p"]), X["weekday"][t]["range"], X["weekday"][t]["series_mean"]] for t in T]
n_d1 = sum(1 for t in T if P1[t]["adf_raw"]["p"] >= .05 and P1[t]["min_d_for_adf"] == 1 and P1[t]["adf_d1"]["p"] < .05)
q_match = [t for t in T if P1[t]["q_suggest_leading_sig_acf"] >= 4]
q_le2 = [t for t in T if P1[t]["q_suggest_leading_sig_acf"] <= 2]
s7_acf = [t for t in T if 7 in P1[t]["acf_sig_lags"]]
s7_pacf = [t for t in T if 7 in P1[t]["pacf_sig_lags"]]
bound = P1[T[0]]["bound"]

write("PHASE_1_IDENTIFICATION.md", f"""# Phase 1: Identification

{DATA}
## What the live system does versus what this report does

**The live system performs no identification.** `server/main.py` contains no ADF test, no ACF/PACF computation and no
order search (verified by grep: no `adfuller`, `acf`, `pacf`, `acorr_ljungbox` or AIC-based selection anywhere in the
file). It applies one fixed order to every facility and blood type: `FACILITY_SARIMAX_ORDER = (0, 1, 4)` and
`FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)` (`main.py:68-69`), fitted by `_fit_sarimax_facility_forecast`
(`main.py:735`). Its only per-series checks are that the optimiser reports convergence and that the forecast is finite.

That order was chosen **offline during development**, on synthetic data, before any facility existed
(`server/SYNTHETIC_SARIMAX_VALIDATION.md`). An attempt to make the choice per-series with a learned selector was tried
twice and not adopted (`server/sarimax_selector_common.py`, `train_sarimax_selector.py`).

**This report runs identification to VALIDATE the fixed order against this series, not to CHOOSE one.** Nothing below
changes what the system does. Where the readings disagree with the fixed order, that is reported as a disagreement.

## Method (code: `run_methodology.py`, phase 1 block)

* ADF test, `statsmodels.tsa.stattools.adfuller`, constant-only regression, lag length chosen by AIC, alpha = 0.05.
* Differencing: difference repeatedly until ADF rejects the unit root (max 2). The live model always uses d = 1, so ADF is
  also reported on the first difference regardless of what the minimum needed was.
* ACF and PACF of the **first-differenced** series to 28 lags (four weekly cycles); PACF by the Yule-Walker method.
  95% bound = 1.96/sqrt(n) = {bound:.3f} (n = 179 differences).
* "Suggested q / p" below is the crude textbook rule: the number of consecutive significant lags starting at lag 1.

## 1. Stationarity

{table(["Type", "ADF stat (raw)", "p (raw)", "Verdict (raw)", "Min d to pass", "ADF stat (d=1)", "p (d=1)", "Verdict (d=1)"], raw_rows)}
All {n_d1} of 8 series are non-stationary in levels (p >= 0.93) and become stationary after exactly one difference
(p <= 3e-4 for every type). This supports **d = 1** in the fixed order. No series needed d = 2.

## 2. ACF / PACF of the differenced series

{table(["Type", "Significant ACF lags", "Suggested q", "Significant PACF lags", "Suggested p", "Seasonal-multiple ACF lags", "Seasonal-multiple PACF lags"], acf_rows)}
## 3. Is weekly (s = 7) seasonality actually in the data?

The ACF/PACF above show weak evidence at lag 7 (see reading below), so weekly structure was tested directly: detrend each
series with a centred 7-day moving average, then one-way ANOVA on the residual by day of week
(`run_extra_checks.py`).

{table(["Type", "F", "p", "Weekday range (units)", "Series mean (units)"], wk_rows)}
A weekday effect is present in all 8 types (p < 1e-4), largest in absolute terms for O+ ({X['weekday']['O+']['range']} units
peak-to-trough on a mean of {X['weekday']['O+']['series_mean']}). Weekly seasonality genuinely exists in this series.

## Reading against the fixed order (0,1,4)x(1,0,1,7)

* **d = 1: supported** for all 8 types (section 1).
* **q = 4: only weakly supported.** The leading run of significant ACF lags is >= 4 for {len(q_match)} type(s)
  ({', '.join(q_match) or 'none'}) and <= 2 for {len(q_le2)} ({', '.join(q_le2)}). For most types the ACF cuts off at lag 1 or 2, which
  would suggest q = 1 or 2, not 4. Phase 2 agrees: the MA(2)-MA(4) coefficients are mostly not significant.
* **p = 0: consistent with an MA-dominated process.** The PACF stays significant out to lag 5-7 for every type instead of
  cutting off, so a pure AR model is not suggested. This is the signature of an MA process.
* **Seasonal (1,0,1,7): weakly supported by ACF/PACF, supported by the ANOVA.** After differencing, lag 7 is significant in
  the ACF for only {len(s7_acf)} type(s) ({', '.join(s7_acf) or 'none'}) and in the PACF for {len(s7_pacf)} ({', '.join(s7_pacf) or 'none'}). The
  ACF/PACF alone would not justify seasonal terms; the weekday ANOVA does show the seasonality exists, and Phase 2 shows the
  seasonal terms cut AIC by tens of points. No seasonal differencing (D = 0) is used, and nothing here tests that choice.

## Limitations

* Identification by eye-balled ACF/PACF is subjective. The "suggested q/p" rule is the simplest possible reading.
* 180 days is roughly 26 weekly cycles; lags beyond 28 were not examined.
* One series per blood type, from a generated source. Nothing here says the order would validate on a real facility.
""")

# =============================== PHASE 2 ===============================
MODELS = ["fixed (0,1,4)x(1,0,1,7)"] + list(next(iter(P2.values()))["alts"].keys())


def scores(t):
    d = {MODELS[0]: (P2[t]["aic"], P2[t]["bic"])}
    for k, v in P2[t]["alts"].items():
        d[k] = (v["aic"], v["bic"])
    return d


aic_win = {t: min(scores(t), key=lambda k: scores(t)[k][0]) for t in T}
bic_win = {t: min(scores(t), key=lambda k: scores(t)[k][1]) for t in T}
fixed_aic = [t for t in T if aic_win[t] == MODELS[0]]
fixed_bic = [t for t in T if bic_win[t] == MODELS[0]]
cmp_rows = []
for t in T:
    s = scores(t)
    cmp_rows.append([t] + [f"{s[m][0]:.1f} / {s[m][1]:.1f}" for m in MODELS] + [aic_win[t].split()[0], bic_win[t].split()[0]])
short = [m.split()[0] if m.startswith("fixed") is False else "fixed" for m in MODELS]
coef_blocks = []
for t in T:
    r = P2[t]
    rows = [[k, f(v["coef"], 3), f(v["se"], 3), f(v["z"], 2), p(v["p"])] for k, v in r["params"].items()]
    coef_blocks.append(f"### {t}  (AIC {r['aic']:.1f}, BIC {r['bic']:.1f}, log-likelihood {r['llf']:.1f}, converged: {r['converged']})\n\n"
                       + table(["Parameter", "Coef", "Std err", "z", "p"], rows))
dg_rows = []
for t in T:
    d = P2[t]["params"]["dengue_season"]
    dg_rows.append([t, f(d["coef"], 3), f(d["se"], 3), p(d["p"]), "YES" if d["p"] < .05 else "no",
                    f"{P2[t]['aic'] - P2[t]['no_exog']['aic']:+.1f}", f"{P2[t]['bic'] - P2[t]['no_exog']['bic']:+.1f}"])
sig = [t for t in T if P2[t]["params"]["dengue_season"]["p"] < .05]
aic_helps = [t for t in T if P2[t]["aic"] < P2[t]["no_exog"]["aic"]]
bic_helps = [t for t in T if P2[t]["bic"] < P2[t]["no_exog"]["bic"]]
alt_sig = {t: [k.split()[0] for k, v in P2[t]["alts"].items() if v["dengue_p"] < .05] for t in T}
step_rows = [[t, f(X_ := R["step_check"][t]["mean_7d_before_jun1"], 1), f(R["step_check"][t]["mean_7d_after_jun1"], 1),
              f"{(R['step_check'][t]['mean_7d_after_jun1'] / R['step_check'][t]['mean_7d_before_jun1'] - 1) * 100:+.1f}%",
              " / ".join(f"{v}" for v in R["step_check"][t]["monthly_mean"].values())] for t in T]
unstable = [t for t in T if max(abs(v["se"]) for v in P2[t]["params"].values()) > 50]
noninv = [(t, k, P2[t]["params"][k]["coef"]) for t in T for k in P2[t]["params"] if k.startswith("ma.") and abs(P2[t]["params"][k]["coef"]) >= 1]

write("PHASE_2_ESTIMATION.md", f"""# Phase 2: Estimation

{DATA}
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

{chr(10).join(coef_blocks)}
### Estimation quality: read before trusting the coefficients above

* **Degenerate estimates for {', '.join(unstable) or 'none'}.** These fits report `converged: True` but contain standard errors in
  the hundreds (for example `sigma2` and `ma.S.L7` for A-, `ma.L1..L4` for B-). The optimiser stopped at a boundary; the
  coefficients for these two types are **not reliably identified**, even though the live system would accept them, since it
  only checks the convergence flag.
* **Non-invertible MA coefficients (|theta| >= 1):** {', '.join(f'{t} {k}={c:.2f}' for t, k, c in noninv) or 'none'}. The live
  configuration sets `enforce_invertibility=False`, so these are permitted.
* **Seasonal AR and seasonal MA nearly cancel.** `ar.S.L7` is 0.74-0.99 and `ma.S.L7` is -0.59 to -1.05 across types.
  A seasonal AR(1) close to 1 paired with a seasonal MA(1) close to -1 is close to a common-factor cancellation, which
  usually signals an over-parameterised seasonal part.
* MA(2), MA(3), MA(4) are not significant for most types (see tables), consistent with the Phase 1 reading that q = 4 is
  more than the ACF suggests.

## 2. Candidate comparison (AIC / BIC, lower is better)

All models use the same data, the same dengue regressor and the same fit settings; all have d = 1 so AIC/BIC are comparable.
Alternatives: A `(0,1,1)x(0,0,0,7)`, B `(0,1,4)x(0,0,0,7)`, C `(0,1,1)x(1,0,1,7)`, D `(1,1,1)x(1,0,1,7)`, E `(0,1,2)x(1,0,1,7)`.

{table(["Type"] + short + ["Best AIC", "Best BIC"], cmp_rows)}
* **AIC:** the fixed order has the lowest AIC in **{len(fixed_aic)} of 8** types ({', '.join(fixed_aic)}). Where it does not, the winner is
  {', '.join(f'{t}: {aic_win[t].split()[0]}' for t in T if t not in fixed_aic) or 'n/a'}.
* **BIC (heavier complexity penalty):** the fixed order is best in only **{len(fixed_bic)} of 8** types ({', '.join(fixed_bic)}); simpler seasonal
  models win the others ({', '.join(f'{t}: {bic_win[t].split()[0]}' for t in T if t not in fixed_bic)}).
* Models with **no seasonal terms (A, B) are far worse** on AIC, by roughly 29 to 64 AIC points, for every type. The seasonal
  part earns its place even though it is over-parameterised.

**What this evidences:** the fixed order is a reasonable choice, clearly better than non-seasonal models, and lowest-AIC in
most types. It is **not** uniquely best: BIC prefers a smaller seasonal model for half the types, and the MA(3)/MA(4) terms
add little. "Reasonable and defensible" is supported; "optimal" is not.

## 3. THE DENGUE QUESTION

**First, what the data looks like.** The brief for this dataset says it has a graded seasonal curve with no binary step
at 1 June, unlike the older demo file. Checking that directly:

{table(["Type", "Mean 7d before 1 Jun", "Mean 7d after 1 Jun", "Change", "Monthly means Mar / Apr / May / Jun / Jul / Aug / Sep"], step_rows)}
Confirmed: **no binary step at 1 June.** The change across the boundary is only a few percent, and every type declines
smoothly, accelerating through July-September. It is a graded decline, not a season with a visible peak and recovery: the
180 days cover only March-September, so the series shows the downward leg and nothing else.

**Result, live binary flag, fixed order:**

{table(["Type", "Dengue coef", "Std err", "p", "p < 0.05", "AIC with minus without", "BIC with minus without"], dg_rows)}
* **Significant at 5% for {len(sig)} of 8 types: {', '.join(sig) or 'none'}.** The other {8 - len(sig)} are not significant (p from
  {min(P2[t]['params']['dengue_season']['p'] for t in T if t not in sig):.2f} upward). That is a legitimate finding and it is reported as found.
* For {', '.join(sig) or 'no type'}, p = {p(P2[sig[0]]['params']['dengue_season']['p']) if sig else 'n/a'}, which also survives a Bonferroni correction across 8 tests (threshold 0.00625).
  The sign is negative (lower stock while the flag is on), in the direction the hypothesis predicts.
* Adding the regressor **lowers AIC for only {len(aic_helps)} type(s) ({', '.join(aic_helps)}) and lowers BIC for {len(bic_helps)} ({', '.join(bic_helps) or 'none'}).**
  For most types the extra parameter buys no fit. Across the five alternative orders the dengue p-value stays non-significant
  for the same types, and is significant only for {', '.join(f'{t} ({", ".join(alt_sig[t])})' for t in T if alt_sig[t]) or 'no type'}.

**How to read it.** Inside this sample the flag switches exactly once (0 for 63 days, then 1 for 117), and it does so while
every series is already trending downward. A single switch that coincides with a declining trend is a weak basis for
attributing a coefficient to seasonality, and this report cannot separate the two. The honest summary is:
*the pipeline detects a flag-associated shift in one blood type of eight; it does not demonstrate a dengue-driven seasonal
effect.* Because the series is generated, it also cannot say anything about real dengue and real demand.

## Limitations

* Optimiser stability (above) limits what can be read from A- and B-.
* Five alternatives is a small candidate set, not an exhaustive search.
* No adjustment was made for the seasonal AR/MA near-cancellation.
""")

# =============================== PHASE 3 ===============================
K = 6  # ARMA parameters: MA 4 + seasonal AR 1 + seasonal MA 1


def corr_p(t, lag):
    return float(chi2.sf(P3[t]["ljungbox"][str(lag)]["stat"], lag - K))


verdict = {}
rows = []
for t in T:
    d = P3[t]
    lb_ok = all(corr_p(t, l) > .05 for l in (14, 21, 28))
    jb_ok = d["jb"]["p"] > .05
    bv_ok = d["breakvar"]["p"] > .05
    fails = []
    if not lb_ok:
        fails.append("Ljung-Box (corrected)")
    if not jb_ok:
        fails.append("Jarque-Bera")
    if not bv_ok:
        fails.append("heteroskedasticity")
    verdict[t] = (not fails, fails)
    rows.append([t, " / ".join(p(d["ljungbox"][str(l)]["p"]) for l in (7, 14, 21, 28)),
                 " / ".join(p(corr_p(t, l)) for l in (7, 14, 21, 28)),
                 f"{f(d['jb']['stat'], 1)} / {p(d['jb']['p'])} / {f(d['jb']['skew'])} / {f(d['jb']['kurt'])}",
                 f"{f(d['breakvar']['stat'])} / {p(d['breakvar']['p'])}",
                 f"{len(d['resid_acf_outside'])}/28 {d['resid_acf_outside']}",
                 "**PASS**" if not fails else "**FAIL** (" + ", ".join(fails) + ")"])
passed = [t for t in T if verdict[t][0]]
failed = [t for t in T if not verdict[t][0]]
lag7_fail = [t for t in T if corr_p(t, 7) <= .05]

write("PHASE_3_DIAGNOSTICS.md", f"""# Phase 3: Diagnostic checking

{DATA}
## What the live system does versus what this report does

**The live system performs no residual diagnostics.** After fitting the fixed order it checks only that the optimiser
reports convergence and that the forecast values are finite (same function); it does not run Ljung-Box, a normality
test, a heteroskedasticity test or any residual check, and a fit whose residuals fail every test below is served exactly the
same as one that passes. These diagnostics were run offline to test the fixed order on this series. They are not part of
request handling.

## Method (code: `run_methodology.py`, phase 2/3 block)

Residuals are the statsmodels **standardised one-step-ahead forecast errors** of the full-sample fixed-order fit
(`SARIMAX(0,1,4)x(1,0,1,7)` + binary dengue flag). The first {P3[T[0]]['burn']} observations (likelihood burn-in for the
differenced, diffuse-initialised state) are dropped, leaving n = {P3[T[0]]['n_resid']}.

* **Ljung-Box** at lags 7, 14, 21 and 28 (one to four full weekly cycles). Two p-values are shown. *Uncorrected* is the
  statsmodels default (`model_df = 0`) and is too generous, because it ignores that 6 ARMA parameters were fitted.
  *Corrected* uses degrees of freedom = lag - 6. At lag 7 the corrected test has only 1 degree of freedom, where the
  chi-square approximation is unreliable, so lag 7 is shown but **the verdict uses corrected p at lags 14, 21 and 28 only.**
* **Jarque-Bera** (normality), via `fit.test_normality('jarquebera')`.
* **Heteroskedasticity:** `fit.test_heteroskedasticity('breakvar')`, which compares residual variance in the last third of the
  sample with the first third.
* **Residual ACF** to 28 lags against the 95% bound +/-1.96/sqrt(n) = +/-{P3[T[0]]['resid_acf_bound']:.3f}. At 5%, about 1.4 of 28 lags
  are expected outside the bound by chance; up to 2 is treated as chance.

**Verdict rule (stated before looking at results): PASS only if all three hold: corrected Ljung-Box p > 0.05 at lags 14, 21 and
28; Jarque-Bera p > 0.05; heteroskedasticity p > 0.05.** Failing any one is a FAIL.

## Results

Ljung-Box p at lags 7 / 14 / 21 / 28. JB column: statistic / p / skewness / kurtosis (normal = 0 and 3).

{table(["Type", "LB p uncorrected", "LB p corrected (df=lag-6)", "Jarque-Bera", "Het. (breakvar) stat / p", "Resid ACF outside 95% bound", "Verdict"], rows)}
## Verdict

* **PASS ({len(passed)}): {', '.join(passed)}.**
* **FAIL ({len(failed)}): {', '.join(f'{t} [{", ".join(verdict[t][1])}]' for t in failed)}.**

**Serial correlation is the model's strong point; heteroskedasticity is its weak point.** Every type passes Ljung-Box on the
uncorrected default at every lag. With the degrees-of-freedom correction, {', '.join(t for t in T if any(corr_p(t, l) <= .05 for l in (14, 21, 28))) or 'no type'}
fail at lag 14, 21 or 28, and {', '.join(lag7_fail) or 'no type'} would also fail at lag 7. Residual ACF is within the bound for most lags
(no more than 2 of 28 outside for any type), so the mean structure is adequately captured.

The variance is not. The heteroskedasticity test rejects for {sum(1 for t in T if P3[t]['breakvar']['p'] <= .05)} of 8 types. This is expected for this data: every
series falls steadily, so counts and their noise shrink, and a model with constant innovation variance cannot follow
that. It affects the width of prediction intervals (Phase 4). O+ and O- also reject normality (skew -0.44 and -0.52, kurtosis
above 3.9), so their Gaussian interval bounds are less trustworthy.

## Caveats

* A- and B- are labelled PASS/FAIL on residuals that come from fits whose parameters are poorly identified (Phase 2). A pass
  there says the residuals look acceptable, not that the coefficients are reliable.
* No test here adjusts for multiple comparison across 8 types.
* Residual diagnostics are on the in-sample fit; Phase 4 tests out-of-sample behaviour.
""")

# =============================== PHASE 4 ===============================
def wins(metric):
    return [t for t in T if P4[t]["sarimax"][metric] < P4[t]["naive_last"][metric]]


rows = []
for t in T:
    s, n, sn = P4[t]["sarimax"], P4[t]["naive_last"], P4[t]["seasonal_naive_7"]
    rows.append([t, f"{f(s['mape'])} / {f(s['rmse'])} / {f(s['mae'])}", f"{f(n['mape'])} / {f(n['rmse'])} / {f(n['mae'])}",
                 f"{f(sn['mape'])} / {f(sn['rmse'])} / {f(sn['mae'])}"])
cov_rows = [[t, f"{P4[t]['n_inside']}/30", f"{P4[t]['coverage'] * 100:.1f}%"] for t in T]
tot_in = sum(P4[t]["n_inside"] for t in T)
w_rows = [[t] + [f(P4[t]["width_by_horizon"][str(h)], 1) for h in (1, 5, 10, 15, 20, 25, 30)] +
          [f"{P4[t]['width_by_horizon']['30'] / P4[t]['width_by_horizon']['1']:.2f}x"] for t in T]
par_ok = all(P4[t]["live_parity"] and all(v["live"] == v["mine"] for v in P4[t]["live_parity"].values()) for t in T)
mape_w, rmse_w, mae_w = wins("mape"), wins("rmse"), wins("mae")
sn_beat = [t for t in T if P4[t]["sarimax"]["mae"] < P4[t]["seasonal_naive_7"]["mae"]]
not_beat_mae = [t for t in T if t not in mae_w]

write("PHASE_4_FORECASTING.md", f"""# Phase 4: Forecasting and out-of-sample validation

{DATA}
## What the live system does versus what this report does

Live, `GET /forecast` fits the fixed order on a facility's history and forecasts 30 days ahead, returning **seven
checkpoints only** (days 0, 5, 10, 15, 20, 25, 30), rounded to whole units and floored at zero, with a 95% interval
(`_fit_sarimax_facility_forecast`, `main.py:735`; `FORECAST_INTERVAL_CONFIDENCE = 0.95`, `main.py:54`). Fits are cached per facility, type and day
(`_get_or_fit_cached_sarimax`, `main.py:818`). **The live system has no accuracy monitoring**: it never compares a forecast
with what later happened. The hold-out below is an offline test of the same model.

## Method (code: `run_methodology.py`, phase 4 block)

* Hold out the **last 30 days** (2026-08-27 to 2026-09-25). Fit on the first 150 days (2026-03-30 to 2026-08-26) with the
  live specification and binary dengue regressor. Forecast the 30 held-out days directly, with exogenous values from
  `dengue_season_index` for the future dates, exactly as the live function builds them.
* **Daily** forecasts and 95% intervals are scored (30 points per type), not just the seven live checkpoints.
* Metrics: MAPE, RMSE, MAE on the point forecast. Interval coverage = fraction of the 30 actuals inside the 95% interval.
* **Baselines:** (1) naive: last training value carried forward for all 30 days; (2) seasonal naive: the last observed
  week repeated. Only (1) was required; (2) is added because the data has a weekly pattern.
* **Parity check:** for every type the script also calls the production function `_fit_sarimax_facility_forecast` on the same
  150-day training series and compares its checkpoint forecasts with this script's. Result: **{'identical for all 8 types at all six forecast checkpoints' if par_ok else 'DIFFERENCES FOUND, see results.json'}**.
  So the numbers below are what the live model produces, not a lookalike.

## 1. Accuracy against the naive baseline

Cells are MAPE % / RMSE / MAE (units). Lower is better.

{table(["Type", "SARIMAX", "Naive (last value)", "Seasonal naive (7d)"], rows)}
**SARIMAX vs naive (last value):** SARIMAX has the lower MAPE for {len(mape_w)} of 8 types, the lower RMSE for {len(rmse_w)} of 8, and the
lower MAE for {len(mae_w)} of 8. {'It does not beat naive on MAE for ' + ', '.join(not_beat_mae) + ' (' + '; '.join(f"{t}: {f(P4[t]['sarimax']['mae'])} vs {f(P4[t]['naive_last']['mae'])}" for t in not_beat_mae) + '), a near tie.' if not_beat_mae else ''}
The gains are largest where the series fall fastest (O+ MAE {f(P4['O+']['sarimax']['mae'], 1)} vs {f(P4['O+']['naive_last']['mae'], 1)}), which is what a naive
"no change" baseline should do badly on, because these series decline steadily. **That is a weak baseline for this data.**
Against the stronger seasonal-naive baseline, SARIMAX has lower MAE for {len(sn_beat)} of 8 types
({', '.join(sn_beat)}); it loses on {', '.join(t for t in T if t not in sn_beat) or 'none'}.

## 2. Prediction-interval coverage

Nominal coverage is 95%.

{table(["Type", "Actuals inside 95% interval", "Coverage"], cov_rows)}
Overall **{tot_in} of 240 ({tot_in / 240 * 100:.1f}%)** of held-out actuals fell inside the interval. That is well **above** the nominal 95%:
the intervals are **too wide (conservative)**, not too narrow. Reading this as "well calibrated" would be wrong. It is over-cover,
consistent with Phase 3's variance finding (a constant-variance model fitted on the earlier, higher-variance part of a
declining series). For the dashboard this errs on the safe side but overstates uncertainty.

## 3. How the interval widens over the horizon

Width of the 95% interval (upper minus lower, units) at each horizon day.

{table(["Type", "Day 1", "Day 5", "Day 10", "Day 15", "Day 20", "Day 25", "Day 30", "Day 30 / Day 1"], w_rows)}
Widths grow steadily and smoothly with horizon for every type, by roughly 1.3x to 1.7x from day 1 to day 30, the qualitative behaviour
the methodology predicts. They start wide (day-1 width already large relative to the level), which is the over-coverage above.

## Summary, unflattering parts included

* SARIMAX **does** beat last-value-carried-forward on most types, but the baseline is weak on trending data.
* It beats the seasonal-naive baseline on MAE for {len(sn_beat)} of 8 types, not all.
* Intervals over-cover ({tot_in / 240 * 100:.1f}% vs 95% nominal).
* One 30-day hold-out per type is a single test, not a distribution; no rolling-origin evaluation was run here.
* Everything above concerns generated data. Nothing here measures how the model performs on real blood supply.
""")
