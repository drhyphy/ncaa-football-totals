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

    def copy_direct_reports(self):
        fixture = SCRIPTS.parent/'model/reports'
        for name in (*PUBLISH.DIRECT_REPORT_SHA256, 'DIRECT_PROBABILITY_RESEARCH_PLAN.md',
                     'direct_probability_results.md', 'DIRECT_PROBABILITY_RESEARCH_AUDIT.md'):
            (self.reports/name).write_bytes((fixture/name).read_bytes())

    def copy_pbp_reports(self):
        fixture = SCRIPTS.parent/'model/reports'
        for name in (*PUBLISH.PBP_REPORT_SHA256, 'PBP_STATE_RESEARCH_PLAN_V2.md', 'PBP_STATE_RESULTS_V2.md',
                     'PBP_IDENTITY_DIAGNOSTIC.md', 'PBP_STATE_RESULTS_AUDIT_V2.md'):
            (self.reports/name).write_bytes((fixture/name).read_bytes())

    def copy_shape_reports(self):
        fixture = SCRIPTS.parent/'model/reports'
        for name in (*PUBLISH.SHAPE_REPORT_SHA256, 'score_shape_results.md', 'SCORE_SHAPE_RESEARCH_PLAN.md',
                     'SCORE_SHAPE_NUMERICAL_AUDIT.md', 'SCORE_SHAPE_SOURCE_CORRECTION.md',
                     'SCORE_SHAPE_SOURCE_CORRECTION.json', 'score_shape_research_plan.json'):
            (self.reports/name).write_bytes((fixture/name).read_bytes())

    def test_shape_preserves_all_distributions_periods_denominators_and_pinned_evidence(self):
        def bundle():
            return json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        self.assertIsNone(bundle()['score_shape_research'])
        self.copy_shape_reports()
        publication = bundle()
        study = publication['score_shape_research']
        original = json.loads((self.reports/'score_shape_results.json').read_text())
        self.assertEqual(study['candidate_order'], list(PUBLISH.SHAPE_CONFIGURATIONS))
        self.assertEqual(study['selected_on_2022_2024'], 'ridge_normal')
        for period,count,conditional,integer,pushes in [('selection_2022_2024',2327,2315,349,12),('reused_2025',852,852,0,0)]:
            self.assertEqual(study[period]['games'],count)
            self.assertEqual(study[period]['configurations'],original[period]['pooled']['configurations'])
            self.assertEqual(study[period]['comparisons'],original[period]['pooled']['comparisons'])
            for candidate in PUBLISH.SHAPE_CONFIGURATIONS:
                row=study[period]['configurations'][candidate]
                self.assertEqual(row['metric_games']['conditional_brier'],conditional)
                self.assertEqual((row['posted_integer_lines'],row['observed_pushes']),(integer,pushes))
            for grouping in ('by_source','by_season'):
                self.assertEqual(study[period][grouping],original[period][grouping])
        self.assertEqual(study['audit']['status'],'passed')
        self.assertEqual(study['audit']['numeric_comparisons'],1042299)
        for key in ('roi_evaluated','live_policy_changes','probability_artifact_promoted','credible_executable_edge_established','actual_integer_line_validation_in_2025'):
            self.assertIs(study[key],False)
        for name,digest in PUBLISH.SHAPE_REPORT_SHA256.items():
            self.assertEqual(publication['source_report_sha256']['model/reports/'+name],digest)
        self.assertEqual(len(study['links']),10)
        for link in study['links']:
            filename=link['url'].rsplit('/',1)[1]
            self.assertEqual(link['sha256'],hashlib.sha256((self.reports/filename).read_bytes()).hexdigest())
        self.assertNotIn('market_score_shape',publication['reports'][PUBLISH.PRIMARY]['pooled'])

    def test_shape_rejects_changed_frozen_artifacts_sources_and_missing_audit(self):
        self.copy_shape_reports()
        for name in (*PUBLISH.SHAPE_REPORT_SHA256,'SCORE_SHAPE_RESEARCH_PLAN.md','SCORE_SHAPE_SOURCE_CORRECTION.md',
                     'SCORE_SHAPE_SOURCE_CORRECTION.json','score_shape_research_plan.json'):
            with self.subTest(name=name):
                path=self.reports/name
                original=path.read_bytes()
                path.write_bytes(original+b'\n')
                with self.assertRaisesRegex(ValueError,'hash changed'):
                    PUBLISH.build_outputs(self.root)
                path.write_bytes(original)
        (self.reports/'score_shape_numerical_audit.json').unlink()
        with self.assertRaises(FileNotFoundError): PUBLISH.build_outputs(self.root)

    def test_shape_semantic_checks_reject_scores_pushes_reliability_and_pair_faults(self):
        original=json.loads((SCRIPTS.parent/'model/reports/score_shape_results.json').read_text())['selection_2022_2024']['pooled']
        for fault in ('model','count','nonfinite','denominator','push','predicted_push','pair','difference','interval','seed','one_week','bins','reliability_count'):
            with self.subTest(fault=fault):
                row=json.loads(json.dumps(original))
                shape=row['configurations']['market_score_shape']
                comparison=row['comparisons'][0]
                if fault=='model': del row['configurations']['market_normal']
                if fault=='count': shape['games']=2326
                if fault=='nonfinite': shape['metrics']['exact_score_nll']=float('nan')
                if fault=='denominator': shape['metric_games']['conditional_brier']=2327
                if fault=='push': shape['observed_pushes']=11
                if fault=='predicted_push': shape['predicted_pushes']=350
                if fault=='pair': row['comparisons'].pop()
                if fault=='difference': comparison['metrics']['three_outcome_nll']['difference']=0
                if fault=='interval': comparison['metrics']['three_outcome_nll']['interval_99']=[1,-1]
                if fault=='seed': comparison['seed']=0
                if fault=='one_week': comparison['metrics']['three_outcome_nll']['week_blocks']=1
                if fault=='bins': shape['reliability'][0]['upper']=.41
                if fault=='reliability_count': shape['reliability'][0]['games']+=1
                with self.assertRaises(ValueError): PUBLISH.shape_summary(row,2327)

    def test_pbp_absent_until_complete_and_preserves_every_model_period_and_audit(self):
        def bundle():
            return json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        self.assertIsNone(bundle()['pbp_state_research'])
        self.copy_pbp_reports()
        publication = bundle()
        study = publication['pbp_state_research']
        original = json.loads((self.reports/'pbp_state_results_v2.json').read_text())
        self.assertEqual(study['candidate_order'], list(PUBLISH.PBP_CONFIGURATIONS))
        self.assertEqual(study['selected_on_2021_2024'], 'opponent_adjusted_ridge')
        self.assertEqual((study['feature_rows'],study['retained_play_rows']),(5008,762297))
        for period,count in [('selection_2021_2024',3061),('reused_2025',852)]:
            self.assertEqual(study[period]['games'], count)
            self.assertEqual(study[period]['configurations'], original[period]['pooled']['metrics'])
            self.assertEqual(study[period]['comparisons'], original[period]['pooled']['comparisons'])
            self.assertEqual(set(study[period]['by_source']),set(original[period]['by_source']))
            self.assertEqual(set(study[period]['by_season']),set(original[period]['by_season']))
        self.assertTrue(study['audit']['passed'])
        self.assertEqual((study['audit']['fit_count'],study['audit']['numeric_comparisons']),(10,1143))
        self.assertEqual(study['audit']['stage_commits']['selection_committed'][:7],'96557bc')
        for key in ('probabilities_evaluated','roi_evaluated','live_policy_changes','credible_executable_edge_established'):
            self.assertIs(study[key],False)
        for name,digest in PUBLISH.PBP_REPORT_SHA256.items():
            self.assertEqual(publication['source_report_sha256']['model/reports/'+name],digest)
        for link in study['links']:
            filename=link['url'].rsplit('/',1)[1]
            self.assertEqual(link['sha256'],hashlib.sha256((self.reports/filename).read_bytes()).hexdigest())
        self.assertNotIn('pbp_state_ridge',publication['reports'][PUBLISH.PRIMARY]['pooled'])

    def test_pbp_rejects_changed_artifact_bytes_missing_audit_and_changed_frozen_links(self):
        self.copy_pbp_reports()
        for name in (*PUBLISH.PBP_REPORT_SHA256,'PBP_STATE_RESEARCH_PLAN_V2.md','PBP_IDENTITY_DIAGNOSTIC.md','PBP_STATE_RESULTS_AUDIT_V2.md'):
            with self.subTest(name=name):
                path=self.reports/name
                original=path.read_bytes()
                path.write_bytes(original+b'\n')
                with self.assertRaisesRegex(ValueError,'hash changed'):
                    PUBLISH.build_outputs(self.root)
                path.write_bytes(original)
        (self.reports/'PBP_STATE_RESULTS_AUDIT_V2.json').unlink()
        with self.assertRaises(FileNotFoundError):
            PUBLISH.build_outputs(self.root)

    def test_pbp_semantic_checks_reject_omissions_invalid_scores_counts_and_intervals(self):
        original=json.loads((SCRIPTS.parent/'model/reports/pbp_state_results_v2.json').read_text())['reused_2025']['pooled']
        for fault in ('model','count','nonfinite','rmse','pair','difference','interval','seed','one_week'):
            with self.subTest(fault=fault):
                row=json.loads(json.dumps(original))
                if fault=='model': del row['metrics']['market_only']
                if fault=='count': row['metrics']['pbp_state_ridge']['games']=851
                if fault=='nonfinite': row['metrics']['pbp_state_ridge']['mse']=float('nan')
                if fault=='rmse': row['metrics']['pbp_state_ridge']['rmse']=1
                if fault=='pair': row['comparisons'].pop()
                if fault=='difference': row['comparisons'][0]['mse_difference']=0
                if fault=='interval': row['comparisons'][0]['mse_interval_95']=[1,-1]
                if fault=='seed': row['comparisons'][0]['seed']=0
                if fault=='one_week': row['comparisons'][0]['week_blocks']=1
                with self.assertRaises(ValueError): PUBLISH.pbp_summary(row,852)

    def test_direct_study_is_absent_until_completed_results_exist(self):
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        self.assertIsNone(bundle['direct_probability_research'])

    def test_direct_preserves_all_six_scores_fixed_selection_and_all_paired_intervals(self):
        self.copy_direct_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        study = bundle['direct_probability_research']
        source = json.loads((self.reports/'direct_probability_results.json').read_text())
        selection = json.loads((self.reports/'direct_probability_selection.json').read_text())
        self.assertEqual(study['candidate_order'], list(PUBLISH.DIRECT_CONFIGURATIONS))
        self.assertEqual(study['selected_configuration'], 'rawridge')
        self.assertEqual(study['selection_2021_2024']['games'], 2406)
        self.assertEqual(study['reused_2025']['games'], 852)
        for period, original in [('selection_2021_2024',selection['summary']),('reused_2025',source['development_2025'])]:
            for name in PUBLISH.DIRECT_CONFIGURATIONS:
                for metric in ('games','log_loss','brier'):
                    self.assertEqual(study[period]['configurations'][name][metric],original['configurations'][name][metric])
            self.assertEqual(study[period]['comparisons'],original['comparisons'])
        self.assertTrue(study['all_new_2025_point_scores_worse_than_raw50'])
        self.assertTrue(study['all_local_2025_comparison_intervals_include_zero'])
        for key in ('roi_evaluated','live_policy_changes','credible_executable_edge_established'):
            self.assertIs(study[key],False)
        self.assertEqual(study['implementation_freeze_commit'][:7],'586423c')
        self.assertEqual(study['selection_commit'][:7],'33e1c5d')
        for name, digest in PUBLISH.DIRECT_REPORT_SHA256.items():
            self.assertEqual(bundle['source_report_sha256']['model/reports/'+name],digest)
        audit = self.reports/'DIRECT_PROBABILITY_RESEARCH_AUDIT.md'
        self.assertEqual(bundle['source_report_sha256']['model/reports/'+audit.name],hashlib.sha256(audit.read_bytes()).hexdigest())
        self.assertTrue(any(link['url'].endswith(audit.name) for link in study['links']))
        self.assertNotIn('context_logit',bundle['reports'][PUBLISH.PRIMARY]['pooled'])

    def test_direct_rejects_changed_plan_selection_result_and_independent_audit_bytes(self):
        self.copy_direct_reports()
        for name in PUBLISH.DIRECT_REPORT_SHA256:
            with self.subTest(name=name):
                path = self.reports/name
                original = path.read_bytes()
                path.write_bytes(original+b'\n')
                with self.assertRaisesRegex(ValueError,'immutable report hash'):
                    PUBLISH.build_outputs(self.root)
                path.write_bytes(original)

    def test_direct_requires_bound_protocol_and_independent_audit(self):
        self.copy_direct_reports()
        path = self.reports/'DIRECT_PROBABILITY_RESEARCH_PLAN.md'
        original = path.read_bytes()
        path.write_bytes(original+b'\nchanged protocol')
        with self.assertRaisesRegex(ValueError,'plan/protocol'):
            PUBLISH.build_outputs(self.root)
        path.write_bytes(original)
        (self.reports/'direct_probability_research_audit.json').unlink()
        with self.assertRaises(FileNotFoundError):
            PUBLISH.build_outputs(self.root)

    def test_direct_semantic_validation_rejects_omitted_models_counts_and_invalid_scores(self):
        fixture = json.loads((SCRIPTS.parent/'model/reports/direct_probability_results.json').read_text())['development_2025']
        for fault in ('configuration','count','score','year','source','comparison','interval'):
            with self.subTest(fault=fault):
                summary = json.loads(json.dumps(fixture))
                if fault == 'configuration': del summary['configurations']['context_logit']
                if fault == 'count': summary['configurations']['raw50']['games'] -= 1
                if fault == 'score': summary['configurations']['raw50']['brier'] = float('nan')
                if fault == 'year': summary['by_year'] = {}
                if fault == 'source': summary['by_source'] = {}
                if fault == 'comparison': summary['comparisons'].pop()
                if fault == 'interval': summary['comparisons'][0]['interval_98_75'] = [0,0]
                with self.assertRaises(ValueError): PUBLISH.direct_summary(summary,('2025',))

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

    def synthetic_ordinary_reports(self):
        """Temporary invented unit-test numbers, never copied into public data."""
        order = list(PUBLISH.ORDINARY_CONFIGURATIONS)
        def summary(n, mses):
            scores = {name: {'games': n, 'mse': mse, 'rmse': mse**.5, 'mae': mse**.5*.8, 'mean_error': 0.}
                      for name, mse in zip(order, mses)}
            return {'games': n, 'configurations': scores, 'comparisons': [
                {'candidate': a, 'reference': b, 'games': n, 'calendar_week_blocks': 5,
                 'mse_difference': scores[a]['mse']-scores[b]['mse'],
                 'mae_difference': scores[a]['mae']-scores[b]['mae'],
                 'mse_difference_interval_95': [-20., 20.], 'mse_difference_interval_98_75': [-30., 30.],
                 'mae_difference_interval_95': [-2., 2.]}
                for a, b in sorted(PUBLISH.ORDINARY_COMPARISONS)]}
        protocol = self.reports/'ORDINARY_MODEL_RESEARCH_PLAN.md'
        protocol.write_text('Synthetic test-only specification; not an experiment result.')
        columns = [f'synthetic_feature_{i}' for i in range(58)]
        plan = {'feature_columns': columns, 'candidate_order': order, 'selection_years': [2021, 2022, 2023, 2024],
                'source_files_sha256': {'reports/ORDINARY_MODEL_RESEARCH_PLAN.md': hashlib.sha256(protocol.read_bytes()).hexdigest()},
                'feature_manifest': {'counts': {'games': 5008}, 'feature_contract': {'availability_proxy': 'synthetic fixture'}}}
        plan['plan_sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        a, b = [200., 195., 190., 205.], [200., 201., 207., 199.]
        selection, last = summary(400, a), summary(100, b)
        result = {'version': 'synthetic-test-only', 'status': 'reused_development_point_prediction_study',
                  'evaluated_at': '2026-09-09T00:00:00Z', 'plan_sha256': plan['plan_sha256'],
                  'source_files_sha256': plan['source_files_sha256'], 'feature_columns': columns,
                  'candidate_order': order, 'primary_metric': 'mse', 'no_2026_outcomes': True,
                  'live_policy_changes': False, 'credible_executable_edge_established': False,
                  'prediction_file_sha256': 'synthetic', 'selected_on_2021_2024': 'ordinary_ridge',
                  'selection_2021_2024': selection, 'reused_2025': last,
                  'by_season': {**{str(y): summary(100, a) for y in (2021, 2022, 2023, 2024)}, '2025': last},
                  'by_market_source': {'synthetic_source': summary(500, [(4*x+y)/5 for x,y in zip(a,b)])},
                  'by_market_source_scope': 'Synthetic fixture only',
                  'by_period_and_market_source': {'selection_2021_2024': {'synthetic_source': selection}, 'reused_2025': {'synthetic_source': last}},
                  'limitations': ['Synthetic unit-test data only']}
        (self.reports/'ordinary_model_research_plan.json').write_text(json.dumps(plan))
        (self.reports/'ordinary_model_research_results.json').write_text(json.dumps(result))
        (self.reports/'ordinary_model_research_results.md').write_text('Synthetic unit-test report only.')
        return result

    def test_ordinary_hook_preserves_four_configs_and_pre2025_choice_without_live_addition(self):
        original = self.synthetic_ordinary_reports()
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        study = bundle['ordinary_model_research']
        self.assertEqual(study['selected_on_2021_2024'], 'ordinary_ridge')
        self.assertEqual(study['selection_2021_2024'], original['selection_2021_2024'])
        self.assertEqual(study['reused_2025'], original['reused_2025'])
        self.assertEqual(len(study['candidate_order']), 4)
        self.assertEqual(len(study['by_season']), 5)
        self.assertFalse(study['live_policy_changes'])
        self.assertFalse(study['probabilities_evaluated'])
        self.assertFalse(study['roi_evaluated'])
        self.assertNotIn('ordinary_ridge', bundle['reports'][PUBLISH.PRIMARY]['pooled'])

    def test_ordinary_rejects_omitted_models_years_and_post2025_winner_substitution(self):
        original = self.synthetic_ordinary_reports()
        path = self.reports/'ordinary_model_research_results.json'
        for mutation, expected in [('model', 'all four'), ('year', 'all five'), ('winner', 'pre-2025 MSE')]:
            result = json.loads(json.dumps(original))
            if mutation == 'model': del result['reused_2025']['configurations']['ordinary_ridge']
            elif mutation == 'year': del result['by_season']['2023']
            else: result['selected_on_2021_2024'] = 'ordinary_hgb'
            path.write_text(json.dumps(result))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, expected):
                PUBLISH.build_outputs(self.root)

    def test_ordinary_rejects_stale_protocol_and_stays_absent_until_results_exist(self):
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        self.assertIsNone(bundle['ordinary_model_research'])
        self.synthetic_ordinary_reports()
        (self.reports/'ORDINARY_MODEL_RESEARCH_PLAN.md').write_text('A changed synthetic plan.')
        with self.assertRaisesRegex(ValueError, 'source/protocol hashes are stale'):
            PUBLISH.build_outputs(self.root)

    def test_completed_ordinary_result_keeps_negative_comparison_and_all_configurations(self):
        fixture = SCRIPTS.parent/'model/reports'
        for name in ('ordinary_model_research_results.json', 'ordinary_model_research_results.md',
                     'ordinary_model_research_plan.json', 'ORDINARY_MODEL_RESEARCH_PLAN.md'):
            (self.reports/name).write_bytes((fixture/name).read_bytes())
        bundle = json.loads(PUBLISH.build_outputs(self.root)[self.root/'site/data/research.json'])
        study = bundle['ordinary_model_research']
        self.assertTrue(study['new_models_higher_mse_than_both_references_in_both_periods'])
        self.assertEqual(study['selected_on_2021_2024'], 'opponent_adjusted_ridge')
        self.assertEqual(study['selection_2021_2024']['games'], 3061)
        self.assertEqual(study['reused_2025']['games'], 852)
        self.assertEqual(len(study['selection_2021_2024']['comparisons']), 4)
        self.assertEqual(len(study['reused_2025']['configurations']), 4)
        self.assertEqual(set(study['by_period_and_market_source']), {'selection_2021_2024', 'reused_2025'})
        self.assertFalse(study['live_policy_changes'])


if __name__ == '__main__':
    unittest.main()
