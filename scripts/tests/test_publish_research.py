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

    def copy_calibration_reports(self):
        fixture = SCRIPTS.parent / 'model/reports'
        for name in ('calibration_research_results.json', 'calibration_research_results.md',
                     'calibration_research_plan.json', 'CALIBRATION_RESEARCH_PLAN.md'):
            (self.reports / name).write_bytes((fixture / name).read_bytes())

    def test_calibration_preserves_all_six_configurations_and_pre2025_selection(self):
        self.copy_calibration_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        study = bundle['calibration_research']
        source = json.loads((self.reports / 'calibration_research_results.json').read_text())
        self.assertEqual(study['configuration_count'], 6)
        self.assertEqual(study['selected_configuration'], 'opponent_adjusted_ridge:raw')
        self.assertEqual(study['periods'], source['periods'])
        self.assertTrue(study['all_ridge_variants_worse_than_raw_market_2025'])
        self.assertFalse(study['roi_evaluated'])
        self.assertFalse(study['live_policy_changed'])
        self.assertFalse(study['credible_new_betting_edge'])
        self.assertNotIn('fits', study)
        key = 'model/reports/calibration_research_results.json'
        self.assertEqual(bundle['source_report_sha256'][key], hashlib.sha256((self.reports / 'calibration_research_results.json').read_bytes()).hexdigest())

    def test_calibration_rejects_omitted_configuration_and_2025_winner_substitution(self):
        self.copy_calibration_reports()
        path = self.reports / 'calibration_research_results.json'
        original = json.loads(path.read_text())
        missing = json.loads(path.read_text())
        del missing['periods']['reused_2025']['opponent_adjusted_ridge:raw']
        path.write_text(json.dumps(missing))
        with self.assertRaisesRegex(ValueError, 'all six'):
            PUBLISH.build_outputs(self.root)
        original['selected_configuration'] = 'market_only:raw'
        path.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, 'pre-2025'):
            PUBLISH.build_outputs(self.root)

    def test_calibration_rejects_stale_plan_source_and_hashes_optional_audit(self):
        self.copy_calibration_reports()
        audit = self.reports / 'CALIBRATION_RESEARCH_AUDIT.md'
        audit.write_text('Independent audit fixture.')
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        self.assertEqual(bundle['source_report_sha256']['model/reports/CALIBRATION_RESEARCH_AUDIT.md'], hashlib.sha256(audit.read_bytes()).hexdigest())
        self.assertTrue(any(link['url'].endswith(audit.name) for link in bundle['calibration_research']['links']))
        path = self.reports / 'CALIBRATION_RESEARCH_PLAN.md'
        path.write_text(path.read_text() + '\nUntracked specification change.\n')
        with self.assertRaisesRegex(ValueError, 'source hash is stale'):
            PUBLISH.build_outputs(self.root)

    def copy_noaa_reports(self):
        fixture = SCRIPTS.parent / 'model/reports'
        for name in ('noaa_weather_results.json', 'noaa_weather_results.md',
                     'noaa_weather_request_plan.json', 'NOAA_WEATHER_EVALUATION_PROTOCOL.md'):
            (self.reports / name).write_bytes((fixture / name).read_bytes())

    def test_noaa_preserves_every_year_source_and_separate_negative_result(self):
        self.copy_noaa_reports()
        self.copy_weather_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root / 'site/data/research.json'])
        study = bundle['weather_shadow']['original_noaa_2021_2023']
        self.assertEqual(set(study['by_season']), {'2021', '2022', '2023'})
        self.assertLess(study['by_season']['2023']['weather_rule']['roi'], 0)
        self.assertEqual(study['pooled']['weather_rule']['bets'], 130)
        self.assertLess(study['pooled']['weather_rule']['roi'], 0)
        self.assertEqual(sum(study['source_game_counts'].values()), 1747)
        self.assertEqual(len(study['by_market_source']), 5)
        self.assertEqual(bundle['weather_shadow']['historical_hypothesis_results']['pooled']['weather_rule']['bets'], 85)
        self.assertFalse(study['combined_with_other_weather_studies'])
        self.assertTrue(study['no_live_policy_changes'])
        self.assertFalse(study['credible_executable_edge_established'])
        self.assertEqual(study['timing_evidence_class'], 'original_s3_metadata_availability_proxy_not_certified_publication')
        self.assertNotIn('weeks', study['pooled'])
        for source in study['by_market_source'].values():
            if source['weather_rule']['bets'] == 0:
                self.assertIsNone(source['weather_rule']['roi'])
        for key in ('weather_rule_roi_95_week_bootstrap', 'weather_rule_roi_99_week_bootstrap',
                    'rule_minus_all_under_roi_95_paired_week_bootstrap', 'rule_minus_all_under_roi_99_paired_week_bootstrap'):
            self.assertLess(study['pooled'][key][0], 0)
            self.assertGreater(study['pooled'][key][1], 0)

    def test_noaa_cannot_omit_losing_year_or_change_timing_and_protocol_claims(self):
        self.copy_noaa_reports()
        path = self.reports / 'noaa_weather_results.json'
        original = path.read_text()
        for modification, expected in (('omit_2023', 'all three'), ('certified_timing', 'availability-proxy'), ('stale_protocol', 'protocol source hash')):
            result = json.loads(original)
            if modification == 'omit_2023':
                del result['by_season']['2023']
            elif modification == 'certified_timing':
                result['timing_evidence_class'] = 'certified'
            else:
                result['protocol_sha256'] = 'stale'
            path.write_text(json.dumps(result))
            with self.subTest(modification=modification), self.assertRaisesRegex(ValueError, expected):
                PUBLISH.build_outputs(self.root)

    def test_noaa_settlement_and_zero_bet_roi_are_checked_before_publication(self):
        self.copy_noaa_reports()
        result = json.loads((self.reports / 'noaa_weather_results.json').read_text())
        zero = next(row for row in result['by_market_source'].values() if row['weather_rule']['bets'] == 0)
        zero['weather_rule']['roi'] = 0.
        with self.assertRaisesRegex(ValueError, 'zero-bet ROI'):
            PUBLISH.noaa_summary(zero)
        result['pooled']['weather_rule']['profit_units'] += 1
        with self.assertRaisesRegex(ValueError, 'settlement'):
            PUBLISH.noaa_summary(result['pooled'])


if __name__ == '__main__':
    unittest.main()
