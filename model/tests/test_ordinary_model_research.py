import unittest

import numpy as np
import pandas as pd

from ncaaf_model import ordinary_model_research as study


def fixture():
    rng = np.random.default_rng(9)
    x = rng.normal(size=330)
    data = pd.DataFrame({'game_id': np.arange(330), 'season': [2020]*310+[2021]*20,
                         'x': x, 'missing': np.nan, 'market_total': 50.,
                         'actual_total': 50+3*x+rng.normal(size=330),
                         'adjusted_history_games': 7})
    data.loc[::11, 'x'] = np.nan
    return data.iloc[:310].copy(), data.iloc[310:].copy()


class OrdinaryModelTests(unittest.TestCase):
    def test_test_outcomes_never_affect_new_model_forecast(self):
        train, test = fixture()
        mutated = test.copy()
        mutated['actual_total'] = np.arange(len(test))*1000
        for name in study.NEW_MODELS:
            with self.subTest(candidate=name):
                first, details = study.fit_fold(train, test, ['x', 'missing'], name)
                second, _ = study.fit_fold(train, mutated, ['x', 'missing'], name)
                np.testing.assert_array_equal(first, second)
                self.assertEqual(details['training_entirely_missing_columns'], ['missing'])
                self.assertEqual(details['training_seasons'], [2020])
                if name == 'ordinary_hgb':
                    self.assertEqual(details['fit_iterations'], 220)

    def test_test_predictors_do_not_change_training_imputation_or_fit(self):
        train, test = fixture()
        extended = pd.concat([test, test.iloc[:1].assign(game_id=500, x=1e9)], ignore_index=True)
        for name in study.NEW_MODELS:
            with self.subTest(candidate=name):
                first, _ = study.fit_fold(train, test, ['x', 'missing'], name)
                second, _ = study.fit_fold(train, extended, ['x', 'missing'], name)
                np.testing.assert_allclose(first, second[:-1], atol=1e-12, rtol=0)

    def test_fold_requires_prior_disjoint_rows(self):
        train, test = fixture()
        with self.assertRaisesRegex(ValueError, 'earlier seasons'):
            study.fit_fold(train.assign(season=2021), test, ['x'], 'ordinary_ridge')
        with self.assertRaisesRegex(ValueError, 'disjoint games'):
            study.fit_fold(train, test.assign(game_id=0), ['x'], 'ordinary_ridge')
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            study.fit_fold(train.iloc[:20], test, ['x'], 'ordinary_ridge')

    def test_predictor_contract_cannot_include_current_outcomes(self):
        train, test = fixture()
        for columns in [['actual_total'], ['home_score'], ['game_id'], ['season'], ['x', 'x'], []]:
            with self.subTest(columns=columns), self.assertRaisesRegex(ValueError, 'predictor contract'):
                study.fit_fold(train, test, columns, 'ordinary_ridge')

    def test_nonfinite_inputs_and_unplanned_model_fail(self):
        train, test = fixture()
        with self.assertRaisesRegex(ValueError, 'Infinite'):
            study.fit_fold(train.assign(x=np.inf), test, ['x'], 'ordinary_ridge')
        with self.assertRaisesRegex(ValueError, 'Invalid target'):
            study.fit_fold(train.assign(actual_total=np.nan), test, ['x'], 'ordinary_ridge')
        with self.assertRaisesRegex(ValueError, 'Unplanned'):
            study.factory('extra_candidate')

    def test_selection_cannot_choose_using_2025(self):
        frame = pd.DataFrame({'season': [2021, 2022, 2023, 2024, 2025], 'actual_total': [50]*5,
            'market_only': [49]*5, 'opponent_adjusted_ridge': [48]*5,
            'ordinary_ridge': [50, 50, 50, 50, 500], 'ordinary_hgb': [47, 47, 47, 47, 50]})
        self.assertEqual(study.select_candidate(frame), 'ordinary_ridge')
        frame.loc[frame.season.eq(2025), 'actual_total'] = 1000
        self.assertEqual(study.select_candidate(frame), 'ordinary_ridge')
        frame.loc[:, list(study.CONFIGURATIONS)] = 50
        self.assertEqual(study.select_candidate(frame), 'market_only')

    def test_paired_weeks_use_eastern_calendar_and_common_denominator(self):
        frame = pd.DataFrame({'game_date': ['2021-09-06T01:00:00Z', '2021-09-06T05:00:00Z', '2021-09-12T22:00:00Z'],
                              'actual_total': [50, 50, 50], 'first': [50, 50, 50], 'second': [51, 52, 53]})
        self.assertEqual(study.weeks(frame).tolist(), ['2021-08-30', '2021-09-06', '2021-09-06'])
        result = study.contrast(frame, 'first', 'second')
        self.assertEqual(result['calendar_week_blocks'], 2)
        self.assertAlmostEqual(result['mse_difference'], -14/3)
        self.assertEqual(result['mse_difference_interval_95'], [-6.5, -1.])
        self.assertEqual(result['mse_difference_interval_98_75'], [-6.5, -1.])
        self.assertEqual(result['mae_difference_interval_95'], [-2.5, -1.])

    def test_one_week_and_metric_signs(self):
        frame = pd.DataFrame({'game_date': ['2021-09-04T20:00:00Z'], 'actual_total': [50], 'first': [48], 'second': [50]})
        result = study.contrast(frame, 'first', 'second')
        self.assertEqual(result['mse_difference'], 4)
        self.assertIsNone(result['mse_difference_interval_95'])
        self.assertEqual(study.scores([50, 60], [52, 54]), {'games': 2, 'mse': 20., 'rmse': np.sqrt(20.), 'mae': 4., 'mean_error': 2.})
        with self.assertRaises(ValueError):
            study.scores([], [])


if __name__ == '__main__':
    unittest.main()
