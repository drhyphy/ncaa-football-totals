"""Evidence publication must preserve losing years and reject stale sources."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publish_research', SCRIPTS / 'publish_research.py')
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.reports = self.root / 'model/reports'
        self.reports.mkdir(parents=True)
        fixture = SCRIPTS.parent / 'model/reports'
        for name in (PUBLISH.PRIMARY, PUBLISH.REPAIR, PUBLISH.AUDIT, PUBLISH.SENSITIVITY):
            (self.reports / name).write_bytes((fixture / name).read_bytes())

    def tearDown(self):
        self.temporary.cleanup()

    def test_bundle_uses_repaired_reports_and_preserves_losing_year(self):
        outputs = PUBLISH.build_outputs(self.root)
        bundle = json.loads(outputs[self.root / 'site/data/research.json'])
        primary = bundle['reports'][PUBLISH.PRIMARY]
        year = primary['by_season']['2025']['opponent_adjusted_ridge']
        self.assertEqual(bundle['data_fingerprint'], primary['data_fingerprint'])
        self.assertFalse(bundle['untouched_test'])
        self.assertFalse(bundle['historical_prices_observed'])
        self.assertIn('2025', outputs[self.reports / 'opponent_adjusted_development.md'])
        self.assertIn(year['roi_display'], outputs[self.reports / 'opponent_adjusted_development.md'])
        self.assertIn('%', year['roi_interval_display'])

    def test_quarantined_metrics_never_become_current_evidence(self):
        (self.reports / 'totals_backtest_summary.json').write_text('{"fake_promotional_roi":999}')
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        self.assertNotIn('totals_backtest_summary.json', bundle['reports'])
        self.assertEqual(bundle['quarantined_reports'][0]['status'], 'quarantined_not_primary_evidence')
        self.assertNotIn('fake_promotional_roi', json.dumps(bundle))

    def test_mismatched_sensitivity_fingerprint_fails_closed(self):
        path = self.reports / PUBLISH.SENSITIVITY
        report = json.loads(path.read_text())
        report['primary_data_fingerprint'] = 'stale'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'stale'):
            PUBLISH.build_outputs(self.root)

    def test_percent_hides_float_noise_without_hiding_real_losses(self):
        self.assertEqual(PUBLISH.percent(-5e-18), '+0.00%')
        self.assertEqual(PUBLISH.percent(-0.045454545), '-4.55%')
        self.assertEqual(PUBLISH.interval(None), '—')

    def copy_weather_reports(self):
        fixture = SCRIPTS.parent / 'model/reports'
        for name in ('weather_request_plan.json', 'weather_published_hypothesis_results.json', 'weather_robustness.json', 'WEATHER_PRIMARY_SOURCE_AUDIT.md'):
            (self.reports / name).write_bytes((fixture / name).read_bytes())

    def test_weather_robustness_is_compact_and_hashes_the_primary_source_audit(self):
        self.copy_weather_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        robust = bundle['weather_shadow']['statistical_robustness']
        family = next(row for row in robust['all_covered_week_cluster_t']['multiplicity_sensitivity'] if row['hypothetical_family_size'] == 4)
        self.assertLess(family['bonferroni_95_family_interval'][0], 0)
        self.assertGreater(family['bonferroni_95_family_interval'][1], 0)
        self.assertNotIn('weeks', robust)
        self.assertNotIn('groups_table', robust['venue_concentration'])
        key = 'model/reports/WEATHER_PRIMARY_SOURCE_AUDIT.md'
        digest = hashlib.sha256((self.reports / 'WEATHER_PRIMARY_SOURCE_AUDIT.md').read_bytes()).hexdigest()
        self.assertEqual(bundle['source_report_sha256'][key], digest)
        self.assertEqual(bundle['weather_shadow']['primary_source_audit']['sha256'], digest)

    def test_weather_robustness_rejects_a_stale_report_hash(self):
        self.copy_weather_reports()
        path = self.reports / 'weather_robustness.json'
        report = json.loads(path.read_text())
        report['input_sha256']['model/reports/weather_published_hypothesis_results.json'] = 'stale'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'source hash is stale'):
            PUBLISH.build_outputs(self.root)

    def copy_replay_reports(self):
        fixture = SCRIPTS.parent / 'model/reports'
        for name in ('archived_2026_replay_results.json', 'archived_2026_replay_plan.json'):
            (self.reports / name).write_bytes((fixture / name).read_bytes())

    def test_archived_replay_preserves_losses_and_is_explicitly_not_prospective(self):
        self.copy_replay_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        replay = bundle['archived_2026_scoring_replay']
        cohort = replay['cohorts']['connected_two_books']
        self.assertFalse(replay['prospective_model_performance'])
        self.assertFalse(replay['exact_0630_replay'])
        self.assertEqual(cohort['snapshot_count'], 14)
        self.assertEqual(len(cohort['snapshots']), 14)
        self.assertEqual(cohort['positions']['opponent_adjusted_ridge']['bets'], 2)
        self.assertEqual(cohort['positions']['opponent_adjusted_ridge']['wins'], 1)
        self.assertEqual(PUBLISH.percent(cohort['positions']['opponent_adjusted_ridge']['roi']), '-3.70%')
        self.assertEqual(PUBLISH.percent(cohort['positions']['opponent_adjusted_structural']['roi']), '-12.13%')
        self.assertIsNone(cohort['positions']['opponent_adjusted_ridge']['roi_95_low'])

    def test_archived_replay_cannot_change_capture_time_without_a_matching_plan(self):
        self.copy_replay_reports()
        path = self.reports / 'archived_2026_replay_results.json'
        replay = json.loads(path.read_text())
        replay['cohorts']['connected_two_books']['snapshots'][0]['observed_at'] = '2026-08-20T10:30:00Z'
        path.write_text(json.dumps(replay))
        with self.assertRaisesRegex(ValueError, 'capture times or hashes differ'):
            PUBLISH.build_outputs(self.root)

    def test_zero_bet_weather_replay_preserves_null_roi_and_separate_cohorts(self):
        fixture = SCRIPTS.parent / 'model/reports'
        for name in ('weather_2026_replay_results.json', 'weather_2026_replay_plan.json'):
            (self.reports / name).write_bytes((fixture / name).read_bytes())
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        replay = bundle['weather_shadow']['archived_2026_replay']
        self.assertFalse(replay['prospective_model_performance'])
        self.assertEqual(len(replay['cohorts']), 2)
        for cohort in replay['cohorts'].values():
            self.assertEqual(cohort['rule']['bets'], 0)
            self.assertIsNone(cohort['rule']['roi'])
            self.assertEqual(cohort['settled_price_eligible_covered_games'], 35)
        self.assertNotIn('pooled', replay)


if __name__ == '__main__':
    unittest.main()
