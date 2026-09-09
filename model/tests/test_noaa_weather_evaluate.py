import copy
from email.utils import format_datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from ncaaf_model import noaa_weather_evaluate as ev
from ncaaf_model import noaa_weather_research as planning


def game_fixture():
    times = planning.forecast_times('2021-09-04T17:30:00+00:00')
    return {'game_id': '1', 'season': 2021, 'week': 1, 'home_id': 10, 'away_id': 20,
            'home_team': 'Home', 'away_team': 'Away', 'market_source': 'cfbd_consensus',
            'venue_id': '42', 'coordinates': {'latitude': 40.125, 'longitude': -90.125},
            'bilinear_points': planning.bilinear_points(40.125, -90.125), **times}


def requests_fixture(game=None):
    game = game or game_fixture()
    rows = []
    for h, lead in enumerate(game['forecast_leads']):
        fields = ['u_wind', 'v_wind'] + (['temperature', 'relative_humidity'] if h == 0 else [])
        rows.append({'request_id': f'r{h}', 'initialization': game['initialization'], 'lead_hours': lead,
                     'source_url': planning.object_url(game['initialization'], lead), 'fields': fields,
                     'last_modified_must_be_strictly_before_utc': game['decision_time'],
                     'games': [{'game_id': game['game_id'], 'hour_offset': h, 'fields': fields,
                                'decision_time': game['decision_time']}]})
    return rows


def metadata_fixture(field='u_wind'):
    request = requests_fixture()[0]
    init = ev.utc(request['initialization'])
    valid = init+pd.Timedelta(hours=request['lead_hours'])
    discipline, category, parameter, level, units = ev.FIELD_SPECS[field]
    return {**ev.GRID, 'edition': 2, 'discipline': discipline, 'parameterCategory': category,
            'parameterNumber': parameter, 'typeOfLevel': 'heightAboveGround', 'level': level,
            'units': units, 'dataDate': int(init.strftime('%Y%m%d')), 'dataTime': init.hour*100,
            'forecastTime': request['lead_hours'], 'stepUnits': 1, 'indicatorOfUnitOfTimeRange': 1,
            'stepType': 'instant', 'validityDate': int(valid.strftime('%Y%m%d')), 'validityTime': valid.hour*100,
            'uvRelativeToGrid': 0}


def hours_fixture(u=5., v=0., kelvin=280., rh=70.):
    return [{'valid_time': stamp, 'fields': {'u_wind': [u]*4, 'v_wind': [v]*4,
            **({'temperature': [kelvin]*4, 'relative_humidity': [rh]*4} if h == 0 else {})}}
            for h, stamp in enumerate(game_fixture()['valid_hours'])]


def scores_fixture(flags=(True, False, True, False)):
    return pd.DataFrame({'game_id': ['1', '2', '3', '4'],
                         'kickoff': ['2021-09-05T23:00:00Z', '2021-09-06T00:30:00Z',
                                     '2021-09-12T23:00:00Z', '2021-09-13T00:30:00Z'],
                         'actual_total': [40, 60, 50, 60], 'market_total': [50., 50., 50., 50.],
                         'shadow_under_flag': list(flags)})


class NoaaEvaluationTests(unittest.TestCase):
    def test_bilinear_conserves_constants_and_uses_correct_wrapped_indices(self):
        points = planning.bilinear_points(40.125, -.125)
        self.assertAlmostEqual(ev.bilinear_value([12]*4, points), 12)
        self.assertAlmostEqual(ev.bilinear_value([0, 4, 8, 12], points), 6)
        self.assertEqual(ev.grid_indexes(points), [200*1440+1439, 200*1440, 199*1440+1439, 199*1440])
        points[0]['weight'] = .5
        with self.assertRaisesRegex(ValueError, 'conserve'):
            ev.grid_indexes(points)

    def test_vector_interpolation_precedes_scalar_speed(self):
        hours = hours_fixture()
        for hour in hours:
            hour['fields']['u_wind'] = [10, -10, 10, -10]
        result = ev.classify_weather(game_fixture(), hours)
        self.assertEqual(result['wind_mph'], 0)
        self.assertFalse(result['shadow_under_flag'])
        self.assertGreater(np.mean(np.abs(hours[0]['fields']['u_wind']))*ev.MPH_PER_MPS, 7.78)

    def test_average_four_scalar_speeds_not_four_component_vectors(self):
        hours = hours_fixture()
        for hour, u in zip(hours, [5, -5, 5, -5]):
            hour['fields']['u_wind'] = [u]*4
        result = ev.classify_weather(game_fixture(), hours)
        self.assertAlmostEqual(result['wind_mph'], 5*ev.MPH_PER_MPS)
        self.assertAlmostEqual(result['temperature_f'], 44.33)
        self.assertEqual(result['relative_humidity_percent'], 70)
        self.assertTrue(result['shadow_under_flag'])

    def test_exact_thresholds_are_strict_and_missing_values_do_not_pass(self):
        result = ev.classify_weather(game_fixture(), hours_fixture(rh=56.8))
        self.assertFalse(result['shadow_under_flag'])
        for field, value in [('relative_humidity', 101), ('temperature', 0), ('u_wind', np.nan)]:
            hours = hours_fixture()
            hours[0]['fields'][field][0] = value
            with self.assertRaisesRegex(ValueError, 'forecast values|Physically invalid'):
                ev.classify_weather(game_fixture(), hours)
        with self.assertRaisesRegex(ValueError, 'four exact'):
            ev.classify_weather(game_fixture(), hours_fixture()[:3])

    def test_metadata_validates_native_hours_orientation_level_and_vector_reference(self):
        request = requests_fixture()[0]
        ev.validate_metadata(metadata_fixture(), request, 'u_wind')
        for field, value in [('stepUnits', 0), ('indicatorOfUnitOfTimeRange', 2), ('level', 2),
                             ('Ni', 720), ('iScansNegatively', 1), ('jScansPositively', 1),
                             ('jPointsAreConsecutive', 1), ('alternativeRowScanning', 1),
                             ('uvRelativeToGrid', 1), ('units', 'mph'), ('forecastTime', 54),
                             ('validityTime', 1800), ('dataTime', 600)]:
            changed = {**metadata_fixture(), field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                ev.validate_metadata(changed, request, 'u_wind')

    def test_datetime_rejects_naive_numeric_and_invalid_grib_dates(self):
        for value in ['2021-09-04 06:30', 1630751400, None, 'nonsense']:
            with self.assertRaises(ValueError):
                ev.utc(value)
        with self.assertRaises(ValueError):
            ev.grib_datetime(20210904, 2460)
        self.assertEqual(ev.grib_datetime(20210904, 30).isoformat(), '2021-09-04T00:30:00+00:00')
        g = game_fixture()
        g['decision_time'] = '2021-09-04T11:30:00+00:00'
        with self.assertRaisesRegex(ValueError, 'timing convention'):
            ev.classify_weather(g, hours_fixture())

    def test_all_ten_game_field_links_required_once_at_exact_cycle(self):
        plan = {'games': [game_fixture()], 'requests': requests_fixture()}
        ev.validate_plan_links(plan)
        for mutation in ('missing', 'duplicate', 'lead'):
            changed = copy.deepcopy(plan)
            if mutation == 'missing':
                changed['requests'].pop()
            elif mutation == 'duplicate':
                changed['requests'].append(changed['requests'][0])
            else:
                changed['requests'][2]['games'][0]['hour_offset'] = 3
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                ev.validate_plan_links(changed)

    def receipt_fixture(self):
        request = requests_fixture()[0]
        raw = b'GRIB'+b'\x00\x00\x00\x02'+(20).to_bytes(8, 'big')+b'7777'
        span = {'range_id': 'field', 'field': 'u_wind', 'source_url': request['source_url'],
                'initialization': request['initialization'], 'lead_hours': request['lead_hours'],
                'start': 100, 'end': 119, 'bytes': 20, 'object_bytes': 500, 'etag': '"abc-7"',
                'last_modified': '2021-09-02T16:30:00+00:00'}
        record = {'range_id': 'field', 'range_sha256': planning.digest(span), 'source_url': span['source_url'],
                  'response_status': 206, 'bytes': 20, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'response_headers': {'Content-Range': 'bytes 100-119/500', 'Content-Length': '20',
                    'ETag': span['etag'], 'Last-Modified': 'Thu, 02 Sep 2021 16:30:00 GMT'}}
        return record, span, request, raw

    def test_receipt_keeps_multipart_but_rejects_wrong_range_hash_and_late_metadata(self):
        record, span, request, raw = self.receipt_fixture()
        ev.validate_receipt(record, span, request, raw)
        for change in ('status', 'range', 'hash', 'late', 'missing_time'):
            modified = copy.deepcopy(record)
            if change == 'status': modified['response_status'] = 200
            elif change == 'range': modified['response_headers']['Content-Range'] = 'bytes 101-120/500'
            elif change == 'hash': modified['sha256'] = 'wrong'
            elif change == 'late': modified['response_headers']['Last-Modified'] = 'Sat, 04 Sep 2021 10:30:00 GMT'
            else: del modified['response_headers']['Last-Modified']
            with self.subTest(change=change), self.assertRaises(ValueError):
                ev.validate_receipt(modified, span, request, raw)

    def test_under_payoffs_include_pushes_in_stake_denominator(self):
        profits = ev.settle_under([40, 60, 50], [50, 50, 50])
        result = ev.metrics(profits)
        self.assertEqual((result['bets'], result['wins'], result['losses'], result['pushes']), (3, 1, 1, 1))
        self.assertAlmostEqual(result['roi'], (100/110-1)/3)
        self.assertEqual(result['win_rate_excluding_pushes'], .5)
        for actual, line in [([np.nan], [50]), ([40.5], [50]), ([40], [5])]:
            with self.assertRaises(ValueError):
                ev.settle_under(actual, line)

    def test_week_resampling_is_paired_and_uses_eastern_calendar(self):
        result = ev.weekly_summary(scores_fixture(flags=(True, True, True, True)), draws=300)
        self.assertEqual([w['week'] for w in result['weeks']], ['2021-08-30', '2021-09-06'])
        self.assertEqual(result['rule_minus_all_under_roi'], 0)
        self.assertEqual(result['rule_minus_all_under_roi_95_paired_week_bootstrap'], [0, 0])
        self.assertEqual(result['rule_minus_all_under_roi_99_paired_week_bootstrap'], [0, 0])
        self.assertEqual(result['nonselected_games']['bets'], 0)

    def test_zero_selection_weeks_remain_in_bootstrap_and_empty_draws_are_counted(self):
        result = ev.weekly_summary(scores_fixture(flags=(True, False, False, False)), draws=1000)
        self.assertEqual(result['calendar_week_blocks'], 2)
        self.assertGreater(result['bootstrap_draws_with_no_rule_bets'], 0)
        self.assertEqual(result['bootstrap_valid_rule_draws']+result['bootstrap_draws_with_no_rule_bets'], 1000)
        self.assertEqual(result['active_week_cluster_t']['status'], 'insufficient_active_weeks')
        self.assertEqual(result['nonselected_games']['bets'], 3)
        self.assertIsNone(result['weeks'][0]['leave_one_week_out_roi'])

    def test_no_bets_or_no_coverage_never_become_zero_roi_evidence(self):
        empty_rule = ev.weekly_summary(scores_fixture(flags=(False, False, False, False)), draws=200)
        self.assertIsNone(empty_rule['weather_rule']['roi'])
        self.assertIsNone(empty_rule['weather_rule_roi_95_week_bootstrap'])
        self.assertEqual(empty_rule['bootstrap_draws_with_no_rule_bets'], 200)
        empty = ev.weekly_summary(scores_fixture().iloc[:0], draws=200)
        self.assertEqual(empty['games'], 0)
        self.assertIsNone(empty['all_under_same_weather_coverage']['roi'])

    def test_cluster_ratio_uses_stakes_not_mean_of_weekly_returns(self):
        result = ev.active_week_cluster_t([1, 3, 0], [1, -1, 0])
        self.assertEqual(result['roi'], 0)
        self.assertEqual(result['active_weeks'], 2)
        self.assertAlmostEqual(result['standard_error'], .5)
        self.assertEqual(result['df'], 1)
        self.assertGreater(result['interval_99'][1], result['interval_95'][1])
        with self.assertRaises(ValueError):
            ev.active_week_cluster_t([1, 0], [1, 1])

    def outcome_fixture(self):
        game = game_fixture()
        classified = {**game, **ev.classify_weather(game, hours_fixture()), 'status': 'available', 'unavailable_reasons': []}
        row = {'game_id': '1', 'season': 2021, 'home_id': 10, 'away_id': 20, 'market_source': 'cfbd_consensus',
               'market_total': 50., 'actual_total': 47, 'home_score': 30, 'away_score': 17, 'status': 'STATUS_FINAL'}
        row.update({key+'_schedule': row[key] for key in ('season', 'home_id', 'away_id', 'home_score', 'away_score', 'status')})
        return {'games': [game]}, {'games': [classified]}, pd.DataFrame([row])

    def test_outcome_join_checks_source_teams_final_status_and_score_identity(self):
        plan, classified, outcomes = self.outcome_fixture()
        covered, excluded = ev.join_classified_outcomes(plan, classified, outcomes)
        self.assertEqual(len(covered), 1)
        self.assertFalse(excluded)
        for field, value in [('away_id', 999), ('season_schedule', 2022), ('status_schedule', 'STATUS_IN_PROGRESS'),
                             ('actual_total', 48), ('market_source', 'inplay'), ('home_score_schedule', 29)]:
            changed = outcomes.copy(); changed.loc[0, field] = value
            covered, excluded = ev.join_classified_outcomes(plan, classified, changed)
            self.assertTrue(covered.empty)
            self.assertEqual(len(excluded), 1)
        with self.assertRaisesRegex(ValueError, 'Duplicate outcome'):
            ev.join_classified_outcomes(plan, classified, pd.concat([outcomes, outcomes]))

    def test_saved_classification_cannot_be_changed_to_fit_an_outcome(self):
        plan, classified, outcomes = self.outcome_fixture()
        classified['games'][0]['shadow_under_flag'] = False
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            ev.join_classified_outcomes(plan, classified, outcomes)

    def test_unavailable_weather_cannot_enter_return_denominators(self):
        plan, classified, outcomes = self.outcome_fixture()
        classified['games'][0].update(status='unavailable', shadow_under_flag=None,
                                     unavailable_reasons=['grid_value_unavailable:relative_humidity'])
        covered, excluded = ev.join_classified_outcomes(plan, classified, outcomes)
        self.assertTrue(covered.empty)
        self.assertIn('weather_unavailable', excluded[0]['reasons'])
        self.assertIn('grid_value_unavailable:relative_humidity', excluded[0]['reasons'])

    def test_repaired_provider_precedes_cfbd_without_outcome_selection(self):
        _, _, outcomes = self.outcome_fixture()
        repaired = outcomes.drop(columns=[key for key in outcomes if key.endswith('_schedule')]).copy()
        repaired.loc[0, 'game_id'] = 1
        repaired.loc[0, 'market_source'] = 'espn_nonlive_provider_58'
        rich = repaired.copy()
        rich.loc[0, 'market_source'] = 'consensus'
        rich.loc[0, 'market_total'] = 55.
        second = rich.copy(); second.loc[0, 'game_id'] = 2
        rich = pd.concat([rich, second], ignore_index=True)
        schedule_columns = ['game_id', 'season', 'home_id', 'away_id', 'home_score', 'away_score', 'status']
        schedules = rich[schedule_columns].copy()
        def read(path, **kwargs):
            name = Path(path).name
            if name.startswith('espn_verified'):
                self.assertIn(('role_verified', '==', True), kwargs['filters'])
                return repaired.copy()
            if name.startswith('cfbd'):
                self.assertIn(('validated', '==', True), kwargs['filters'])
                return rich.copy()
            return schedules.copy() if '2021' in name else schedules.iloc[:0].copy()
        with patch.object(ev.pd, 'read_parquet', side_effect=read):
            joined = ev.load_outcomes(Path('/synthetic-only'))
        self.assertEqual(len(joined), 2)
        first = joined.set_index('game_id').loc['1']
        self.assertEqual(first.market_source, 'espn_nonlive_provider_58')
        self.assertEqual(first.market_total, 50.)
        self.assertEqual(joined.set_index('game_id').loc['2'].market_source, 'cfbd_consensus')

    def test_evaluation_requires_saved_classification_before_any_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ev, 'read_classifications', side_effect=ValueError('not yet classified')):
                with patch.object(ev, 'load_outcomes') as outcomes:
                    with self.assertRaisesRegex(ValueError, 'not yet classified'):
                        ev.evaluate(Path(directory))
                    outcomes.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('eccodes'), 'ecCodes decoder optional outside NOAA research')
    def test_real_grib_decoder_indexes_and_missing_bitmap_on_synthetic_grid(self):
        import eccodes
        game, request = game_fixture(), requests_fixture()[0]
        for missing in (False, True):
            gid = eccodes.codes_grib_new_from_samples('regular_ll_sfc_grib2')
            try:
                metadata = metadata_fixture()
                for key in ('Ni', 'Nj', 'latitudeOfFirstGridPointInDegrees', 'longitudeOfFirstGridPointInDegrees',
                            'latitudeOfLastGridPointInDegrees', 'longitudeOfLastGridPointInDegrees',
                            'iDirectionIncrementInDegrees', 'jDirectionIncrementInDegrees', 'discipline',
                            'parameterCategory', 'parameterNumber', 'typeOfLevel', 'level', 'dataDate', 'dataTime',
                            'stepUnits', 'forecastTime'):
                    eccodes.codes_set(gid, key, metadata[key])
                values = np.zeros(ev.GRID['numberOfDataPoints'])
                indexes = ev.grid_indexes(game['bilinear_points'])
                values[indexes] = [10, 20, 30, 40]
                if missing:
                    eccodes.codes_set(gid, 'bitmapPresent', 1)
                    values[indexes[0]] = eccodes.codes_get(gid, 'missingValue')
                eccodes.codes_set_values(gid, values)
                raw = eccodes.codes_get_message(gid)
            finally:
                eccodes.codes_release(gid)
            result = ev.decode_points(raw, request, 'u_wind', {'1': game['bilinear_points']})
            if missing:
                self.assertEqual(result['games']['1']['status'], 'unavailable')
                self.assertEqual(result['games']['1']['reason'], 'required_grid_point_missing')
            else:
                self.assertEqual(result['games']['1']['values'], [10, 20, 30, 40])


if __name__ == '__main__':
    unittest.main()
