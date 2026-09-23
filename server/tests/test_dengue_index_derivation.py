"""Tests for derive_dengue_season_index.py — the reproducibility artifact
for the graded-dengue-index investigation recorded in
DENGUE_INDEX_DERIVATION.md (not wired into main.py; see that doc and
dengue_season_index()'s docstring for why).

These exist so the two candidate climatologies documented in that markdown
file stay reproducible from the committed CSV — if someone edits the CSV or
the derivation method, this catches the drift, the same purpose
test_prediction_interval.py's hand-derived expected values serve for the OLS
math.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_dengue_index_derivation -v
"""

import unittest

from derive_dengue_season_index import load_rows, monthly_climatology, yearly_peak_and_total

# The originally-specified values — confirmed in DENGUE_INDEX_DERIVATION.md
# to be exactly what pooling 2016-2019 (2019, the epidemic year, included)
# produces from the real CSV.
POOLED_2016_2019 = {
    1: 0.27, 2: 0.23, 3: 0.16, 4: 0.08, 5: 0.10, 6: 0.18,
    7: 0.44, 8: 0.84, 9: 1.00, 10: 0.58, 11: 0.44, 12: 0.29,
}

# The alternative consistent with this repo's own prior precedent
# (SYNTHETIC_SARIMAX_VALIDATION.md excludes 2019 as a declared epidemic
# year) — 2016-2018 only.
TYPICAL_2016_2018 = {
    1: 0.43, 2: 0.31, 3: 0.20, 4: 0.12, 5: 0.14, 6: 0.26,
    7: 0.63, 8: 0.96, 9: 1.00, 10: 0.77, 11: 0.64, 12: 0.47,
}


class DengueIndexDerivationTests(unittest.TestCase):
    def test_2016_2018_reproduces_the_typical_year_climatology(self):
        rows = load_rows()
        result = monthly_climatology(rows, {2016, 2017, 2018})
        self.assertEqual(result, TYPICAL_2016_2018)

    def test_2016_2019_reproduces_the_originally_given_values(self):
        rows = load_rows()
        result = monthly_climatology(rows, {2016, 2017, 2018, 2019})
        self.assertEqual(result, POOLED_2016_2019)

    def test_september_is_the_peak_under_either_window(self):
        rows = load_rows()
        for years in ({2016, 2017, 2018}, {2016, 2017, 2018, 2019}):
            with self.subTest(years=years):
                result = monthly_climatology(rows, years)
                self.assertEqual(max(result, key=result.get), 9)
                self.assertEqual(result[9], 1.00)

    def test_2019_is_a_real_outlier_year_not_an_arbitrary_exclusion(self):
        # The concrete evidence DENGUE_INDEX_DERIVATION.md's exclusion
        # rationale rests on: 2019's peak week and yearly total should be
        # dramatically higher than every other typical year, consistent
        # with the declared national dengue epidemic that year.
        rows = load_rows()
        stats = yearly_peak_and_total(rows)
        peak_2019, total_2019, _ = stats[2019]
        for year in (2016, 2017, 2018):
            peak, total, _ = stats[year]
            with self.subTest(year=year):
                self.assertGreater(peak_2019, peak * 3)
                self.assertGreater(total_2019, total * 2)

    def test_2020_and_2021_case_counts_collapsed_versus_typical_years(self):
        # The COVID-suppression rationale: 2020's peak week should be far
        # below any typical year's, and 2021 should have almost no data on
        # file at all (this dataset only has 2 weeks for it).
        rows = load_rows()
        stats = yearly_peak_and_total(rows)
        peak_2018, _, _ = stats[2018]
        peak_2020, _, _ = stats[2020]
        _, _, weeks_2021 = stats[2021]
        self.assertLess(peak_2020, peak_2018 / 1.3)
        self.assertLessEqual(weeks_2021, 5)


if __name__ == "__main__":
    unittest.main()
