"""Tamper detection against the first immutable public capture, offline only."""
import importlib.util
import contextlib
from datetime import timedelta
import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / 'model'
RELATIVE = Path('data/runtime/weather_revisions/runs/local-20260909T032436108097Z-1.json')
SPEC = importlib.util.spec_from_file_location('independent_revision_audit', REPO / 'scripts/audit_weather_revision_capture.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def profile_manifest(name='season'):
    begin, end = (AUDIT.START, AUDIT.END) if name == 'pilot' else (AUDIT.END, AUDIT.SEASON_END)
    protocol = AUDIT.PILOT_PROTOCOL if name == 'pilot' else AUDIT.SEASON_PROTOCOL
    return {'collection_profile': name,
            'collection_protocol_id': 'weather-revision-capture-pilot-v1' if name == 'pilot' else 'weather-revision-season-collection-v1',
            'collection_protocol_file': protocol,
            'collection_window': {'start_inclusive': AUDIT.iso(begin), 'end_exclusive': AUDIT.iso(end)},
            'scheduled_utc_slots': list(AUDIT.SCHEDULED_UTC_SLOTS),
            'capture_started_at': AUDIT.iso(begin), 'capture_completed_at': AUDIT.iso(begin + timedelta(seconds=5)),
            'provenance': {AUDIT.PILOT_PROTOCOL: 'a' * 64, **({AUDIT.SEASON_PROTOCOL: 'b' * 64} if name == 'season' else {})}}


class ProfileAdmissionTests(unittest.TestCase):
    def test_legacy_pilot_keeps_original_window_without_new_metadata(self):
        manifest = {k: v for k, v in profile_manifest('pilot').items()
                    if not k.startswith('collection_') and k != 'scheduled_utc_slots'}
        result = AUDIT.collection_profile(manifest)
        self.assertEqual(result['collection_profile'], 'pilot')
        self.assertTrue(result['legacy_pilot_profile'])
        for date in (AUDIT.START - timedelta(microseconds=1), AUDIT.END):
            with self.subTest(date=date):
                manifest['capture_started_at'] = AUDIT.iso(date)
                manifest['capture_completed_at'] = AUDIT.iso(date + timedelta(seconds=5))
                with self.assertRaisesRegex(AssertionError, 'outside declared'):
                    AUDIT.collection_profile(manifest)

    def test_explicit_pilot_has_no_season_admission(self):
        manifest = profile_manifest('pilot')
        self.assertFalse(AUDIT.collection_profile(manifest)['legacy_pilot_profile'])
        manifest['capture_started_at'] = AUDIT.iso(AUDIT.END)
        manifest['capture_completed_at'] = AUDIT.iso(AUDIT.END + timedelta(seconds=5))
        with self.assertRaisesRegex(AssertionError, 'outside declared'):
            AUDIT.collection_profile(manifest)

    def test_season_exact_boundaries_allow_completion_after_admitted_start(self):
        for date, admitted in ((AUDIT.END - timedelta(microseconds=1), False), (AUDIT.END, True),
                               (AUDIT.SEASON_END - timedelta(microseconds=1), True), (AUDIT.SEASON_END, False)):
            with self.subTest(date=date):
                manifest = profile_manifest()
                manifest['capture_started_at'] = AUDIT.iso(date)
                manifest['capture_completed_at'] = AUDIT.iso(date + timedelta(seconds=5))
                if admitted:
                    self.assertEqual(AUDIT.collection_profile(manifest)['collection_profile'], 'season')
                else:
                    with self.assertRaisesRegex(AssertionError, 'outside declared'):
                        AUDIT.collection_profile(manifest)

    def test_unknown_partial_or_mismatched_explicit_profile_fails(self):
        changes = [('collection_profile', 'future'), ('collection_profile', None),
                   ('collection_protocol_id', 'weather-revision-capture-pilot-v1'),
                   ('collection_protocol_file', AUDIT.PILOT_PROTOCOL),
                   ('collection_window', {'start_inclusive': AUDIT.iso(AUDIT.START), 'end_exclusive': AUDIT.iso(AUDIT.SEASON_END)}),
                   ('scheduled_utc_slots', ['07:17'])]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                manifest = profile_manifest()
                manifest[key] = value
                with self.assertRaises(AssertionError):
                    AUDIT.collection_profile(manifest)
        manifest = profile_manifest('pilot')
        del manifest['collection_profile']
        with self.assertRaisesRegex(AssertionError, 'Partial collection'):
            AUDIT.collection_profile(manifest)

    def test_season_requires_both_original_and_continuation_protocol_references(self):
        for missing in (AUDIT.PILOT_PROTOCOL, AUDIT.SEASON_PROTOCOL):
            with self.subTest(missing=missing):
                manifest = profile_manifest()
                del manifest['provenance'][missing]
                with self.assertRaisesRegex(AssertionError, 'protocol provenance missing'):
                    AUDIT.collection_profile(manifest)


class SyntheticSeasonAuditTests(unittest.TestCase):
    """An empty official slate still traverses real hashes, Git blobs and audit."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = Path(self.directory.name)
        self.root = self.repository / 'model'
        self.root.mkdir()
        source_bodies = {
            'ncaaf_model/teams.py': b'ALIASES = {}\n',
            'data/models/weather_venues_v1.json': (MODEL / 'data/models/weather_venues_v1.json').read_bytes(),
            AUDIT.PILOT_PROTOCOL: b'Original synthetic pilot protocol, separately versioned.\n',
            AUDIT.SEASON_PROTOCOL: b'Synthetic season continuation, separately versioned.\n'}
        for relative, body in source_bodies.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        subprocess.run(['git', 'init', '-q', str(self.repository)], check=True, capture_output=True)
        subprocess.run(['git', 'add', 'model'], cwd=self.repository, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Independent Audit Test', '-c', 'user.email=audit@example.invalid',
                        'commit', '-qm', 'Synthetic frozen sources'], cwd=self.repository, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repository, text=True).strip()
        self.manifest = profile_manifest()
        self.manifest['provenance'] = {p: AUDIT.sha(b) for p, b in source_bodies.items()}
        begin = AUDIT.END
        body = b'{"events": []}'
        body_path = 'data/runtime/weather_revisions/bodies/' + AUDIT.sha(body) + '.json.gz'
        path = self.root / body_path
        path.parent.mkdir(parents=True)
        path.write_bytes(gzip.compress(body))
        receipt = {'schema_version': 'raw-http-receipt-v1', 'purpose': 'official_frozen_cohort',
                   'requested_at': AUDIT.iso(begin), 'received_at': AUDIT.iso(begin + timedelta(seconds=1)),
                   'request': {'url': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard', 'params': {'groups': 80}},
                   'response_headers': {}, 'status_code': 200, 'transport_error': None, 'body_withheld': False,
                   'body_path': body_path, 'body_sha256': AUDIT.sha(body), 'body_size_bytes': len(body)}
        relative = 'data/runtime/weather_revisions/receipts/' + AUDIT.digest(receipt) + '.json'
        receipt['receipt_path'] = relative
        path = self.root / relative
        path.parent.mkdir()
        path.write_text(json.dumps(receipt))
        cohort_path = 'data/runtime/weather_revisions/cohorts/synthetic-season.json'
        path = self.root / cohort_path
        path.parent.mkdir()
        path.write_text(json.dumps({'capture_started_at': AUDIT.iso(begin), 'inventory_receipt': relative,
                                    'frozen_at': AUDIT.iso(begin + timedelta(seconds=2)), 'games': []}))
        self.manifest.update(schema_version='weather-revision-capture-v1', collector_version='weather-revision-collector-v2',
                             git_commit=commit, run_id='synthetic-season', run_attempt='1', status='no_games',
                             receipts=[relative], inventory_receipt=relative, cohort_path=cohort_path,
                             enumerated_games=0, rows=[], stored_response_bytes=len(body),
                             counts={**{k: 0 for k in ('cohort_games', 'weather_available_games', 'mature_comparator_games',
                                                       'two_book_games', 'paired_games', 'paired_two_book_games', 'failed_requests')},
                                     'total_requests': 1})
        self.path = self.root / 'manifest.json'

    def audit(self):
        self.path.write_text(json.dumps(self.manifest))
        return AUDIT.Audit(self.root, repository=self.repository).execute(self.path)

    def test_actual_recorded_season_protocol_git_blob_verified_with_receipt(self):
        result = self.audit()
        self.assertTrue(result['audit_passed'])
        self.assertEqual(result['collection_profile'], 'season')
        self.assertEqual(result['current_capture_receipts'], 1)
        self.assertTrue(result['provenance'][AUDIT.SEASON_PROTOCOL]['recorded_commit_matches'])
        self.assertTrue(result['recorded_commit_provenance_verified'])

    def test_changed_current_season_protocol_does_not_replace_recorded_blob(self):
        (self.root / AUDIT.SEASON_PROTOCOL).write_text('Later separately versioned changes.\n')
        result = self.audit()
        self.assertFalse(result['provenance'][AUDIT.SEASON_PROTOCOL]['current_matches'])
        self.assertTrue(result['provenance'][AUDIT.SEASON_PROTOCOL]['recorded_commit_matches'])

    def test_wrong_recorded_season_protocol_hash_fails(self):
        self.manifest['provenance'][AUDIT.SEASON_PROTOCOL] = '0' * 64
        with self.assertRaisesRegex(AssertionError, 'Git source hash mismatch'):
            self.audit()


class AuditOutputTests(unittest.TestCase):
    def test_custom_output_uses_sibling_markdown_without_overwriting_default_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / 'reports'
            reports.mkdir()
            original = reports / 'WEATHER_REVISION_FIRST_CAPTURE_AUDIT.md'
            original.write_text('Preserve first immutable capture report.\n')
            result = {'collection_profile': 'season', 'run_id': 'synthetic', 'run_attempt': '1', 'audit_passed': True,
                      'capture_status': 'no_games', 'current_capture_receipts': 1, 'counts_reconstructed': {'weather_available_games': 0},
                      'normalized_quote_pairs_reconstructed': 0, 'weather_quote_links_reconstructed': 0, 'odds_requests': 0,
                      'recorded_commit_provenance_verified': True, 'recorded_git_commit': 'a' * 40, 'manifest_sha256': 'b' * 64}
            custom = reports / 'weather_revision_season_capture_audit.json'
            base_args = ['audit', '--root', str(root), '--manifest', str(root / 'synthetic.json')]
            with patch.object(AUDIT, 'Audit') as mock, contextlib.redirect_stdout(io.StringIO()):
                mock.return_value.execute.return_value = result
                with patch('sys.argv', base_args + ['--output', str(custom)]):
                    AUDIT.main()
                self.assertEqual(original.read_text(), 'Preserve first immutable capture report.\n')
                self.assertIn('Independent season', custom.with_suffix('.md').read_text())
                with patch('sys.argv', base_args):
                    AUDIT.main()
                self.assertTrue((reports / 'weather_revision_capture_audit.json').exists())
                self.assertIn('Run `synthetic`', original.read_text())


@unittest.skipUnless((MODEL / RELATIVE).exists(), 'First immutable pilot fixture is not present')
class IndependentCaptureAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manifest = json.loads((MODEL / RELATIVE).read_text())
        sources = set(self.manifest['provenance']) | {str(RELATIVE), self.manifest['cohort_path']}
        for path in self.manifest['receipts']:
            sources.add(path)
            body = json.loads((MODEL / path).read_text())['body_path']
            if body:
                sources.add(body)
        for relative in sources:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(MODEL / relative, target)

    def write_manifest(self):
        (self.root / RELATIVE).write_text(json.dumps(self.manifest))

    def audit(self):
        return AUDIT.Audit(self.root, repository=REPO).execute(self.root / RELATIVE)

    def test_real_receipts_reconstruct_without_importing_collector_or_parser(self):
        result = self.audit()
        self.assertTrue(result['audit_passed'])
        self.assertEqual(result['current_capture_receipts'], 307)
        self.assertEqual(result['normalized_quote_pairs_reconstructed'], 128)
        self.assertEqual(result['weather_quote_links_reconstructed'], 94)
        self.assertFalse(result['outcomes_extracted'])
        self.assertTrue(result['recorded_commit_provenance_verified'])

    def test_later_source_change_does_not_invalidate_recorded_git_blobs(self):
        source = self.root / 'ncaaf_model/revision_archive.py'
        source.write_text('Later versioned compatibility correction.\n')
        result = self.audit()
        provenance = result['provenance']['ncaaf_model/revision_archive.py']
        self.assertFalse(provenance['current_matches'])
        self.assertTrue(provenance['recorded_commit_matches'])

    def test_aliases_and_catalog_are_taken_from_verified_historical_blobs(self):
        (self.root / 'ncaaf_model/teams.py').write_text('Not executable Python or a usable alias map.\n')
        (self.root / 'data/models/weather_venues_v1.json').write_text('{}')
        result = self.audit()
        self.assertFalse(result['provenance']['ncaaf_model/teams.py']['current_matches'])
        self.assertFalse(result['provenance']['data/models/weather_venues_v1.json']['current_matches'])
        self.assertEqual(result['normalized_quote_pairs_reconstructed'], 128)

    def test_recorded_source_hash_cannot_be_replaced_by_current_tree_hash(self):
        self.manifest['provenance']['ncaaf_model/revision_archive.py'] = '0' * 64
        self.write_manifest()
        with self.assertRaisesRegex(AssertionError, 'Git source hash mismatch'):
            self.audit()

    def test_missing_recorded_commit_is_not_silently_replaced_by_head(self):
        self.manifest['git_commit'] = '0' * 40
        self.write_manifest()
        result = self.audit()
        self.assertFalse(result['recorded_commit_provenance_verified'])
        for provenance in result['provenance'].values():
            self.assertIsNone(provenance['recorded_commit_matches'])
            self.assertTrue(provenance['git_blob_unavailable'])

    def test_altered_measurement_fails_even_when_manifest_is_valid_json(self):
        row = next(r for r in self.manifest['rows'] if r['single_run'])
        row['single_run']['measurement']['wind_mph_four_hour_mean'] += 0.25
        self.write_manifest()
        with self.assertRaises(AssertionError):
            self.audit()

    def test_altered_offered_price_fails_even_with_recomputed_quote_identifier(self):
        row = next(r for r in self.manifest['rows'] if r['quotes'])
        quote = row['quotes'][0]
        old_id = quote['quote_id']
        quote['under_decimal_odds'] += 0.01
        quote['quote_id'] = AUDIT.digest({k: v for k, v in quote.items() if k != 'quote_id'})
        for pair in row['pairs']:
            if pair['quote_id'] == old_id:
                pair['quote_id'] = quote['quote_id']
        self.write_manifest()
        with self.assertRaises(AssertionError):
            self.audit()

    def test_archived_receipt_timestamp_tampering_is_detected(self):
        path = self.root / self.manifest['receipts'][0]
        receipt = json.loads(path.read_text())
        receipt['received_at'] = '2026-09-09T03:24:36Z'
        path.write_text(json.dumps(receipt))
        with self.assertRaises(AssertionError):
            self.audit()

    def test_context_link_cannot_be_switched_to_another_game(self):
        row = next(r for r in self.manifest['rows'] if r['pairs'])
        row['pairs'][0]['context_id'] = '0' * 64
        self.write_manifest()
        with self.assertRaises(AssertionError):
            self.audit()


if __name__ == '__main__':
    unittest.main()
