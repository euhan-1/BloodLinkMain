"""
SYNTHETIC TEST DATA GENERATOR — not real facility data.

Generates a deterministic, reproducible 2-year daily time series of blood
unit counts per type, with a seasonal demand spike during dengue season
(June-October, matching DENGUE_SEASON_MONTHS in main.py) — modeling the same
"elevated draw rate" narrative already used throughout this app's
shortage-forecast copy, not an arbitrary new assumption.

Modeling choice, stated explicitly: "seasonal pattern that spikes during
dengue season" is implemented as a spike in *demand* (draw rate), which
shows up in the unit-count series as *lower stock levels* during those
months — not a spike upward in the raw numbers. This matches the existing
Predictive Shortage Alert language ("historically elevated draw rate for
this type in Q3") already in this codebase.

Model: each blood type has a smooth seasonal target level — baseline
outside dengue season, dipping to a trough (cosine-shaped, centered on
mid-August) during it. Periodic restocks (donation drives / shipments)
top the level back up relative to *that day's* target, and daily draws
deplete it proportionally to the same target, so the series tracks the
seasonal curve without long-run drift in either direction (a first version
that added/subtracted fixed deltas each cycle had no mean reversion and
either ran away unboundedly or got clamped at zero 20%+ of the time —
this version was tuned against that failure via
verify_synthetic_forecast_data.py before being finalized).

Safe to re-run: clears previously-generated synthetic rows and regenerates
from scratch each time, since this is throwaway model-development data, not
something meant to accumulate indefinitely like the real inventory_snapshots.
"""

import math
import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from database import engine

BLOOD_TYPES = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]

# Seasonal shape and amplitude calibrated against real DOH Epidemiology
# Bureau weekly dengue surveillance counts for Batangas province, 2016-2018
# (see SYNTHETIC_SARIMAX_VALIDATION.md, "Seasonal calibration" — the specific
# dates/years in this generator are still synthetic; only the *shape* of the
# seasonal curve is real). 2019 was excluded from calibration as a declared
# national dengue epidemic year (peak weeks 3-4x a typical year) and 2020-21
# as COVID-lockdown-suppressed (weekly counts collapsed to single digits) —
# neither is representative of a normal season.
DENGUE_PEAK_MONTH_DAY = (9, 10)  # real peak week ~Sep 10, not the previously assumed mid-August
SEASON_RISE_DAYS = 84  # real: cases climb from baseline to peak over ~84 days (~Jun 18 -> Sep 10)
SEASON_FALL_DAYS = 112  # real: cases fall from peak back to baseline over ~112 days (~Sep 10 -> ~Jan 1) — slower than the rise, so the season is asymmetric, not a symmetric window
SEASON_TROUGH_MULTIPLIER = 0.39  # trough/baseline case-count ratio in the real data (~0.39), used as the stand-in for the stock-level dip's amplitude

NUM_DAYS = 730  # 2 years
RANDOM_SEED = 42  # fixed for reproducibility — same dataset every run until intentionally changed

BASELINE_MULTIPLIER = 1.4  # healthy baseline = this x the real minimum-threshold policy value
DAILY_DRAW_FRACTION = 0.06  # fraction of *that day's seasonal target* drawn per day
RESTOCK_INTERVAL_RANGE = (5, 9)  # days between periodic restocks (donation drives / shipments)
RESTOCK_CEILING_RANGE = (1.15, 1.35)  # restock brings level to this x that day's seasonal target
NOISE_STD_FRACTION = 0.04  # daily draw noise, as a fraction of that day's seasonal target


def _fetch_thresholds() -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT blood_type, minimum_units FROM blood_type_thresholds")
        ).mappings().all()
    return {row["blood_type"]: row["minimum_units"] for row in rows}


def _seasonal_multiplier(current_date: date) -> float:
    """1.0 outside dengue season; cosine dip to SEASON_TROUGH_MULTIPLIER at
    the peak. Asymmetric on purpose — the real Batangas data this is
    calibrated against climbs to its peak faster than it falls back off
    (SEASON_RISE_DAYS < SEASON_FALL_DAYS), so the two sides of the season use
    different half-widths rather than one symmetric window, joined smoothly
    at the peak (both sides evaluate to the same trough value there)."""
    doy = current_date.timetuple().tm_yday
    peak_doy = date(current_date.year, *DENGUE_PEAK_MONTH_DAY).timetuple().tm_yday
    diff = doy - peak_doy
    # handle wraparound for years where the season's rise or fall tail
    # crosses the Dec 31 -> Jan 1 boundary
    if diff > 182:
        diff -= 365
    elif diff < -182:
        diff += 365

    dist = abs(diff)
    half_width = SEASON_FALL_DAYS if diff >= 0 else SEASON_RISE_DAYS
    if dist > half_width:
        return 1.0
    frac_from_peak = dist / half_width  # 0 at peak, 1 at that side's season edge
    # cosine ramp: smooth, no discontinuity entering/leaving the season window.
    # frac_from_peak=0 (at the peak) must give TROUGH; frac_from_peak=1 (at
    # the edge) must give 1.0 (baseline) — a fixed phase-inversion bug
    # (present before this recalibration) had this backwards: the previous
    # `(1 - frac_from_peak)` term put the trough at the season EDGE and
    # baseline AT the peak, which produced a double-dip shape (troughs on
    # both sides of the nominal peak with a baseline "bump" sitting exactly
    # on the peak date) rather than the single smooth dip every description
    # of this generator — including the pre-recalibration
    # SYNTHETIC_SARIMAX_VALIDATION.md — has always claimed it produces.
    return SEASON_TROUGH_MULTIPLIER + (1.0 - SEASON_TROUGH_MULTIPLIER) * (
        1 - math.cos(math.pi * frac_from_peak)
    ) / 2


def generate_series(threshold: int, start_date: date, rng: random.Random) -> list[tuple[date, int]]:
    baseline = threshold * BASELINE_MULTIPLIER
    level = baseline
    days_until_restock = rng.randint(*RESTOCK_INTERVAL_RANGE)
    series: list[tuple[date, int]] = []

    for i in range(NUM_DAYS):
        current_date = start_date + timedelta(days=i)
        target = baseline * _seasonal_multiplier(current_date)

        days_until_restock -= 1
        if days_until_restock <= 0:
            ceiling = rng.uniform(*RESTOCK_CEILING_RANGE)
            level = target * ceiling
            days_until_restock = rng.randint(*RESTOCK_INTERVAL_RANGE)

        draw = target * DAILY_DRAW_FRACTION + rng.gauss(0, target * NOISE_STD_FRACTION)
        level -= max(0.0, draw)
        level = max(0.0, level)

        series.append((current_date, round(level)))

    return series


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_synthetic_forecast_data.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

    thresholds = _fetch_thresholds()
    if not thresholds:
        raise RuntimeError("blood_type_thresholds is empty — run seed_thresholds.py first")

    start_date = date.today() - timedelta(days=NUM_DAYS)
    rng = random.Random(RANDOM_SEED)

    all_rows: list[tuple[date, str, int]] = []
    for blood_type in BLOOD_TYPES:
        threshold = thresholds.get(blood_type)
        if threshold is None:
            raise RuntimeError(f"no threshold configured for {blood_type!r}")
        series = generate_series(threshold, start_date, rng)
        all_rows.extend((d, blood_type, units) for d, units in series)

    insert_params = [
        {"snapshot_date": d, "blood_type": blood_type, "units": units} for d, blood_type, units in all_rows
    ]
    with engine.begin() as conn:
        deleted = conn.execute(text("DELETE FROM synthetic_inventory_snapshots")).rowcount
        # Passing a list of param dicts triggers executemany — one batched
        # round trip instead of ~5,800 individual ones.
        conn.execute(
            text(
                """
                INSERT INTO synthetic_inventory_snapshots (snapshot_date, blood_type, units)
                VALUES (:snapshot_date, :blood_type, :units)
                """
            ),
            insert_params,
        )

    print(f"cleared {deleted} old synthetic rows")
    print(f"generated {len(all_rows)} new synthetic rows ({len(BLOOD_TYPES)} types x {NUM_DAYS} days)")
    print(f"date range: {start_date} to {start_date + timedelta(days=NUM_DAYS - 1)}")


if __name__ == "__main__":
    main()
