"""Closed-form checks for weekly ratio inference and cross-role team deletion."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

SPEC = importlib.util.spec_from_file_location('weather_robustness', Path(__file__).resolve().parents[1] / 'weather_robustness.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WeatherRobustnessTests(unittest.TestCase):
    def test_equal_cluster_size_matches_standard_error_of_weekly_means(self):
        means = np.array([.5, -.25, 0., .75])
        result = MODULE.cluster_ratio([4] * 4, means * 4)
        self.assertAlmostEqual(result['roi'], means.mean())
        self.assertAlmostEqual(result['standard_error'], means.std(ddof=1) / 2)
        self.assertEqual(result['df'], 3)

    def test_unequal_week_counts_use_profit_per_bet_not_mean_weekly_roi(self):
        result = MODULE.cluster_ratio([1, 3], [1., -1.])
        self.assertEqual(result['roi'], 0.)
        self.assertAlmostEqual(result['standard_error'], .5)
        self.assertNotAlmostEqual(result['roi'], (1 - 1 / 3) / 2)

    def test_empty_weeks_have_zero_score_and_active_week_sensitivity_is_wider(self):
        active = MODULE.cluster_ratio([2, 3, 1], [1., -1., 0.])
        all_weeks = MODULE.cluster_ratio([2, 3, 1, 0], [1., -1., 0., 0.])
        self.assertAlmostEqual(active['standard_error'], np.sqrt(1 / 12))
        self.assertEqual(all_weeks['roi'], active['roi'])
        self.assertEqual(all_weeks['active_clusters'], 3)
        self.assertLess(active['interval_95'][0], all_weeks['interval_95'][0])

    def test_stricter_confidence_and_multiplicity_expand_intervals(self):
        result = MODULE.cluster_ratio([2, 3, 1], [1., -1., 0.])
        self.assertLess(result['interval_99'][0], result['interval_95'][0])
        cases = result['multiplicity_sensitivity']
        self.assertEqual(cases[0]['bonferroni_95_family_interval'], result['interval_95'])
        for previous, current in zip(cases, cases[1:]):
            self.assertLess(current['bonferroni_95_family_interval'][0], previous['bonferroni_95_family_interval'][0])

    def test_invalid_counts_cannot_create_a_confidence_interval(self):
        for n, p in [([1], [0]), ([0, 0], [0, 0]), ([1, -1], [1, -1]), ([0, 1], [1, 0]), ([1, 1], [1, float('nan')])]:
            with self.assertRaises(ValueError):
                MODULE.cluster_ratio(n, p)

    def test_team_deletion_merges_home_and_away_identity_without_double_counting(self):
        frame = pd.DataFrame({'home_id': [1, 2, 3], 'away_id': [2, 1, 4],
                              'home_team': ['A', 'B', 'C'], 'away_team': ['B', 'A', 'D'],
                              'profit_units': [1., -1., .5]})
        result = MODULE.team_involvement(frame)
        team = next(row for row in result['teams'] if row['team_id'] == '1')
        self.assertEqual(team['games'], 2)
        self.assertEqual(team['home_games'], 1)
        self.assertEqual(team['away_games'], 1)
        self.assertEqual(team['leave_one_team_out_roi'], .5)
        self.assertAlmostEqual(sum(row['appearance_share'] for row in result['teams']), 1.)


if __name__ == '__main__':
    unittest.main()
