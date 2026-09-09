"""Repricing may change settlement, never the frozen weather selection."""
import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('weather_source_sensitivity', SCRIPTS / 'weather_source_sensitivity.py')
SENSITIVITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SENSITIVITY)
sys.path.insert(0, str(SCRIPTS.parent / 'model'))
from ncaaf_model.weather_research import summarize


class WeatherSourceTests(unittest.TestCase):
    def setUp(self):
        self.primary = pd.DataFrame({
            'game_id': [1, 2, 3], 'season': [2024]*3,
            'kickoff': ['2024-09-07T20:00:00Z']*3,
            'actual_total': [44., 50., 61.], 'market_total': [44.5, 48.5, 50.5],
            'shadow_under_flag': [True, False, True], 'wind_mph': [12., 3., 9.],
            'market_source': ['approved_primary']*3,
        })
        self.secondary = pd.DataFrame({
            'game_id': [1, 2], 'season': [2024]*2,
            'actual_total': [44., 50.], 'market_total': [44., 51.],
            'market_source': ['secondary']*2, 'validated': [True]*2,
        })

    def test_only_total_changes_and_missing_source_does_not_gain_a_quote(self):
        primary, repriced, _ = SENSITIVITY.common_cohorts(self.primary, self.secondary)
        self.assertEqual(primary.game_id.tolist(), [1, 2])
        pd.testing.assert_frame_equal(primary.drop(columns='market_total'), repriced.drop(columns='market_total'))
        self.assertEqual(repriced.shadow_under_flag.tolist(), [True, False])
        self.assertEqual(repriced.market_total.tolist(), [44., 51.])

    def test_integer_secondary_line_refunds_push_for_the_identical_selection(self):
        primary, repriced, _ = SENSITIVITY.common_cohorts(self.primary, self.secondary)
        before, after = summarize(primary), summarize(repriced)
        self.assertEqual(before['weather_rule']['bets'], after['weather_rule']['bets'])
        self.assertEqual(before['weather_rule']['wins'], 1)
        self.assertEqual(after['weather_rule']['pushes'], 1)
        self.assertEqual(after['weather_rule']['profit_units'], 0.)
        self.assertEqual(after['weather_rule']['roi'], 0.)

    def test_score_conflict_stops_comparison(self):
        self.secondary.loc[0, 'actual_total'] = 45.
        with self.assertRaisesRegex(ValueError, 'outcome conflicts'):
            SENSITIVITY.common_cohorts(self.primary, self.secondary)


if __name__ == '__main__':
    unittest.main()
