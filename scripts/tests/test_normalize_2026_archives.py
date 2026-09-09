"""Offline archive integrity, pairing, and outcome-separation regression checks."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
spec = importlib.util.spec_from_file_location('normalize_archives', ROOT / 'scripts/normalize_2026_archives.py')
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)


class ArchiveNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        self.source = Path(self.temporary.name) / 'captures'
        schedules = self.root / 'model/data/raw/sportsdataverse'
        schedules.mkdir(parents=True)
        pd.DataFrame([dict(game_id=401000001, season=2026, week=1,
            game_date='2026-09-05T19:00:00Z', home_id=333, away_id=2,
            home_team='Alabama Crimson Tide', away_team='Auburn Tigers', neutral_site=False,
            home_score=99, away_score=98, status='STATUS_FINAL')]).to_parquet(schedules / 'cfb_schedule_2026.parquet')
        raw_dir = self.source / 'data/raw/the_odds_api'
        raw_dir.mkdir(parents=True)
        # Filename deliberately disagrees: only metadata supplies receipt time.
        self.raw = raw_dir / 'ncaaf_19990101T000000Z.json'
        self.body = [dict(id='archived-event', commence_time='2026-09-05T19:00:00Z',
            home_team='Alabama Crimson Tide', away_team='Auburn Tigers',
            bookmakers=[dict(key='draftkings', last_update='2026-09-05T13:00:00Z', markets=[
                dict(key='totals', outcomes=[dict(name='Over', point=52.5, price=-112),
                                            dict(name='Under', point=52.5, price=102)]),
                dict(key='spreads', outcomes=[dict(name='Alabama Crimson Tide', point=-3.5)])])])]

    def write_source(self, *, corrupt_hash=False):
        self.raw.write_text(json.dumps(self.body))
        digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.raw.with_suffix('.meta.json').write_text(json.dumps(dict(
            retrieved_at='2026-09-05T13:01:00Z', sha256='0'*64 if corrupt_hash else digest)))

    def normalize(self):
        with redirect_stdout(io.StringIO()):
            normalizer.main(self.root, self.source)
        return pd.read_parquet(self.root / 'model/data/raw/alternative/archived_2026_paired_quotes.parquet')

    def test_exact_pair_actual_receipt_and_no_outcomes_read(self):
        self.write_source()
        real_read = pd.read_parquet
        with patch.object(normalizer.pd, 'read_parquet', wraps=real_read) as reads:
            frame = self.normalize()
        requested = reads.call_args_list[0].kwargs['columns']
        self.assertTrue({'home_score', 'away_score', 'status'}.isdisjoint(requested))
        row = frame.iloc[0]
        self.assertEqual(row.observed_at, pd.Timestamp('2026-09-05T13:01:00Z'))
        self.assertEqual((row.over_price, row.under_price), (-112, 102))
        self.assertAlmostEqual(row.over_decimal_odds, 1+100/112)
        self.assertEqual(row.under_decimal_odds, 2.02)
        self.assertEqual(row.market_home_spread, -3.5)
        self.assertTrue(row.same_eastern_gameday_after0630)
        self.assertFalse(row.outcomes_loaded)
        self.assertNotIn('home_score', frame)

    def test_hash_mismatch_fails_closed(self):
        self.write_source(corrupt_hash=True)
        with self.assertRaisesRegex(ValueError, 'bad_receipt_or_hash'):
            self.normalize()

    def test_update_after_receipt_quarantines_entire_file(self):
        self.body[0]['bookmakers'][0]['last_update'] = '2026-09-05T13:01:01Z'
        self.write_source()
        with self.assertRaisesRegex(ValueError, 'source_file_chronology_conflict'):
            self.normalize()

    def test_duplicate_or_mismatched_outcomes_are_not_a_pair(self):
        outcomes = self.body[0]['bookmakers'][0]['markets'][0]['outcomes']
        for invalid in ([outcomes[0], outcomes[0], outcomes[1]],
                        [outcomes[0], {**outcomes[1], 'point': 53.5}]):
            with self.subTest(outcomes=invalid):
                self.body[0]['bookmakers'][0]['markets'][0]['outcomes'] = invalid
                self.write_source()
                with self.assertRaisesRegex(ValueError, 'unpaired_total'):
                    self.normalize()

    def test_missing_update_stays_missing(self):
        self.body[0]['bookmakers'][0].pop('last_update')
        self.write_source()
        frame = self.normalize()
        self.assertTrue(pd.isna(frame.iloc[0].market_updated_at))
        self.assertFalse(frame.iloc[0].market_timestamp_present)

    def write_espn(self, *, state='pre', provider_id='100'):
        directory = self.source / 'data/raw/espn_scoreboard'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'scoreboard.json'
        path.write_text(json.dumps({'events': [{'id': '401000001', 'competitions': [{
            'date': '2026-09-05T19:00:00Z', 'status': {'type': {'state': state}},
            'competitors': [{'homeAway': 'home', 'team': {'id': '333'}},
                            {'homeAway': 'away', 'team': {'id': '2'}}],
            'odds': [{'provider': {'id': provider_id, 'name': 'DraftKings'},
                      'overUnder': 52.5, 'total': {
                          'over': {'close': {'line': 'o52.5', 'odds': '-112'}},
                          'under': {'close': {'line': 'u52.5', 'odds': '-108'}}}}]}]}]}))
        path.with_suffix('.meta.json').write_text(json.dumps({
            'retrieved_at': '2026-09-05T13:01:00Z',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))

    def test_espn_nested_current_pair_has_no_invented_timestamp(self):
        self.write_espn()
        row = self.normalize().iloc[0]
        self.assertEqual((row.book, row.line, row.over_price, row.under_price),
                         ('draftkings', 52.5, -112, -108))
        self.assertTrue(pd.isna(row.market_updated_at))
        self.assertIn('not_final_closing_claim', row.source_prestate)

    def test_espn_nonpregame_or_unapproved_provider_rejected(self):
        for kwargs in ({'state': 'in'}, {'provider_id': '59'}):
            with self.subTest(kwargs=kwargs):
                self.write_espn(**kwargs)
                with self.assertRaisesRegex(ValueError, 'No valid paired quotes'):
                    self.normalize()

    def test_preflight_rejects_corrupted_normalized_schema(self):
        self.write_source()
        original = self.normalize()
        mutations = [dict(actual_total=80), dict(outcomes_loaded=True),
                     dict(over_decimal_odds=2.5), dict(within14days=False),
                     dict(observed_at=pd.Timestamp('2026-09-05T20:00:00Z')),
                     dict(market_updated_at=pd.Timestamp('2026-09-05T13:02:00Z'))]
        for changes in mutations:
            with self.subTest(changes=changes):
                frame = original.assign(**changes)
                with self.assertRaises(ValueError):
                    normalizer.validate_quote_frame(frame)
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            normalizer.validate_quote_frame(pd.concat([original, original], ignore_index=True))


if __name__ == '__main__':
    unittest.main()
