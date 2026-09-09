import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from ncaaf_model import noaa_weather_research as noaa


def event(game_id, season):
    return {'id': str(game_id), 'date': f'{season}-09-10T17:30:00Z',
        'competitions': [{'neutralSite': False,
            'venue': {'id': '42', 'fullName': 'Synthetic Stadium', 'indoor': False},
            'competitors': [{'homeAway': 'home', 'score': '99', 'team': {'id': '10'}},
                            {'homeAway': 'away', 'score': '98', 'team': {'id': '20'}}]}]}


class NoaaPlanTests(unittest.TestCase):
    def test_identity_parser_ignores_scores_and_odds(self):
        source = event(1, 2021)
        source['competitions'][0]['odds'] = [{'overUnder': 199}]
        row = noaa.parse_venue_identities({'events': [source]})[0]
        self.assertEqual(row['venue_id'], '42')
        self.assertFalse(row['indoor'])
        self.assertFalse(row['neutral_site'])
        self.assertNotIn('score', json.dumps(row))
        self.assertNotIn('overUnder', row)

    def test_same_initialization_four_hours_and_48h_minimum(self):
        times = noaa.forecast_times('2021-09-04T17:30:00Z')
        self.assertEqual(times['initialization'], '2021-09-02T12:00:00+00:00')
        self.assertEqual(times['forecast_leads'], [53, 54, 55, 56])
        self.assertEqual(times['decision_time'], '2021-09-04T10:30:00+00:00')

    def test_dst_decision_is_wall_clock0630(self):
        self.assertEqual(noaa.forecast_times('2021-11-07T18:00:00Z')['decision_time'],
                         '2021-11-07T11:30:00+00:00')
        with self.assertRaisesRegex(ValueError, 'decision availability'):
            noaa.forecast_times('2021-09-04T10:00:00Z')

    def test_bilinear_weights_and_longitude_wrap(self):
        points = noaa.bilinear_points(40.125, -.125)
        self.assertEqual({p['longitude_0_360'] for p in points}, {0., 359.75})
        self.assertEqual([p['weight'] for p in points], [.25]*4)
        self.assertAlmostEqual(sum(p['weight'] for p in points), 1.)

    def fixture(self, root):
        alt = root/'data/raw/alternative'
        raw = root/'data/raw/sportsdataverse'
        alt.mkdir(parents=True); raw.mkdir(parents=True)
        (root/'data/models').mkdir(parents=True)
        (root/noaa.CATALOG).write_text(json.dumps({'venues': {
            str(i): {'latitude': 40.1, 'longitude': -90.1} for i in range(100)}}))
        rows = []
        for y in noaa.SEASONS:
            row = {'game_id': y, 'season': y, 'week': 2, 'home_id': 10, 'away_id': 20,
                   'home_team': 'Home', 'away_team': 'Away', 'market_source': 'source',
                   'market_total': 52.5, 'validated': True, 'role_verified': True,
                   'game_date': f'{y}-09-10T17:30:00Z', 'neutral_site': False,
                   'venue': 'Synthetic Stadium', 'actual_total': 197, 'home_score': 99, 'away_score': 98}
            rows.append(row)
            pd.DataFrame([row]).to_parquet(raw/f'cfb_schedule_{y}.parquet')
            response_path = root/f'raw_capture_{y}.json'
            response_path.write_text(json.dumps({'events': [event(y, y)]+[
                event(y*10000+i, y) for i in range(499)]}))
            noaa.ingest_venue_capture(root, y, response_path)
        pd.DataFrame(rows).to_parquet(alt/'cfbd_market_games.parquet')
        pd.DataFrame([rows[-1]]).to_parquet(alt/'espn_verified_pregame_games.parquet')

    def test_plan_pins_sources_groups_fields_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            real_read = pd.read_parquet
            with patch.object(noaa.pd, 'read_parquet', wraps=real_read) as reads:
                plan = noaa.build_plan(root)
            for call in reads.call_args_list:
                self.assertTrue({'home_score', 'away_score', 'actual_total'}.isdisjoint(call.kwargs['columns']))
            self.assertEqual(plan['counts']['included_games'], 3)
            self.assertEqual(plan['counts']['forecast_objects'], 12)
            self.assertEqual(plan['counts']['field_range_requests'], 30)
            self.assertFalse(plan['outcomes_used_in_this_plan'])
            self.assertEqual(plan['budget']['forecast_bytes_downloaded_for_this_plan'], 0)
            first_request = plan['requests'][0]
            self.assertEqual(set(first_request['fields']), set(noaa.FIELD_SELECTORS))
            self.assertEqual(noaa.read_plan(root)['plan_sha256'], plan['plan_sha256'])
            with self.assertRaises(FileExistsError):
                noaa.build_plan(root)
            (root/noaa.CATALOG).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Frozen input changed'):
                noaa.read_plan(root)

    def test_sparse_venue_response_rejected_before_archiving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'response.json'
            path.write_text(json.dumps({'events': [event(1, 2021)]}))
            with self.assertRaisesRegex(ValueError, 'sparse'):
                noaa.ingest_venue_capture(root, 2021, path)
            self.assertFalse((root/noaa.RAW).exists())


if __name__ == '__main__':
    unittest.main()
