"""Evidence publication must preserve losing years and reject stale sources."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
