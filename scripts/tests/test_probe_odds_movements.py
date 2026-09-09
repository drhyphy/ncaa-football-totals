import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import requests

SCRIPT = Path(__file__).resolve().parents[1] / 'probe_odds_movements.py'
SPEC = importlib.util.spec_from_file_location('movement_probe', SCRIPT)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class Session:
    def __init__(self, status=200, remaining='80'):
        self.status, self.remaining, self.calls = status, remaining, 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = requests.Response()
        response._content = b'{"movements": []}'
        response.status_code = self.status
        if self.remaining is not None:
            response.headers['X-RateLimit-Remaining'] = self.remaining
        return response


class MovementProbeTests(unittest.TestCase):
    def test_bound_survives_resume_and_duplicate_label_makes_no_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = PROBE.Probe(root, 'sample', 'private-test', max_requests=2)
            session = Session()
            probe.client.session = session
            probe.fetch('first', '/odds/multi', {'eventIds': '1'})
            with self.assertRaises(RuntimeError):
                probe.fetch('first', '/odds/multi', {'eventIds': '1'})
            resumed = PROBE.Probe(root, 'sample', 'private-test', max_requests=2)
            resumed.client.session = session
            resumed.fetch('second', '/odds/movements', {'eventId': '1'})
            with self.assertRaises(RuntimeError):
                resumed.fetch('third', '/odds/movements', {'eventId': '1'})
            self.assertEqual(session.calls, 2)

    def test_auth_failure_and_reported_reserve_stop_without_retry(self):
        for status, remaining in [(403, '80'), (429, '80'), (200, '20'), (200, '26'), (200, None)]:
            with self.subTest(status=status, remaining=remaining), tempfile.TemporaryDirectory() as directory:
                probe = PROBE.Probe(Path(directory), 'sample', 'private-test')
                session = Session(status, remaining)
                probe.client.session = session
                probe.fetch('first', '/odds/multi', {'eventIds': '1'})
                with self.assertRaises(RuntimeError):
                    probe.fetch('second', '/odds/movements', {'eventId': '1'})
                self.assertEqual(session.calls, 1)
                resumed = PROBE.Probe(Path(directory), 'sample', 'private-test')
                resumed.client.session = session
                with self.assertRaises(RuntimeError):
                    resumed.fetch('second', '/odds/movements', {'eventId': '1'})
                self.assertEqual(session.calls, 1)

    def test_only_named_read_endpoints_and_safe_unique_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = PROBE.Probe(Path(directory), 'sample', 'private-test')
            session = Session()
            probe.client.session = session
            for label, path in [('first', '/bookmakers/selected/add'), ('../first', '/odds/multi')]:
                with self.assertRaises(ValueError):
                    probe.fetch(label, path, {})
            self.assertEqual(session.calls, 0)

    def test_schema_summary_does_not_print_historical_outcomes(self):
        payload = {'scores': {'home': 68, 'away': 41}, 'bookmakers': {'DraftKings': [{'name': 'Totals'}]}}
        encoded = json.dumps(PROBE.shape(payload))
        self.assertNotIn('68', encoded)
        self.assertNotIn('41', encoded)
        self.assertIn('scores', encoded)


if __name__ == '__main__':
    unittest.main()
