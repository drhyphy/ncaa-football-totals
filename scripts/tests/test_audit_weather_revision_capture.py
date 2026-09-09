"""Tamper detection against the first immutable public capture, offline only."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / 'model'
RELATIVE = Path('data/runtime/weather_revisions/runs/local-20260909T032436108097Z-1.json')
SPEC = importlib.util.spec_from_file_location('independent_revision_audit', REPO / 'scripts/audit_weather_revision_capture.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


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
