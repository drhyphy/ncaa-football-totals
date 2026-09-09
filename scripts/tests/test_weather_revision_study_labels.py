"""Synthetic public-response storage and label cadence; no external requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

import requests

SPEC = importlib.util.spec_from_file_location('study_labels', Path(__file__).parents[1] / 'weather_revision_study_labels.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
archive = __import__('ncaaf_model.revision_archive', fromlist=['ArchiveClient'])

KICKOFF = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)
NOW = KICKOFF + timedelta(hours=7)


def observation(gid=1, line=54.5):
    gid = str(gid)
    context = {'game_id': gid, 'home_id': str(100 + int(gid) * 2), 'away_id': str(101 + int(gid) * 2),
               'home_team': 'Home ' + gid, 'away_team': 'Away ' + gid, 'venue_id': '3793',
               'kickoff': module._iso(KICKOFF), 'neutral_site': False, 'indoor': False, 'state': 'pre'}
    features = {k: 0. for k in module.dataset.FEATURES}
    features['market_total'] = line
    value = {'schema_version': 'weather-revision-observation-v1', 'adapter_version': module.dataset.VERSION,
             'game_id': gid, 'context_id': module.digest_json(context), 'context': context,
             'kickoff': module._iso(KICKOFF), 'decision_at': module._iso(KICKOFF - timedelta(hours=36)),
             'reference_line': line, 'features': features, 'q_under': .5,
             'probability_input_eligible': line % 1 == .5, 'movement_input_eligible': True,
             'observed_offers': [], 't1': {'run_id': 'synthetic', 'run_attempt': '1'}}
    value['observation_id'] = module.digest_json(value)
    return value


def payload(obs, *, complete=True, total=38):
    context = obs['context']
    return {'header': {'id': obs['game_id'], 'competitions': [{
        'id': obs['game_id'], 'date': obs['kickoff'], 'dateValid': True, 'neutralSite': False,
        'status': {'type': {'state': 'post' if complete else 'in', 'completed': complete,
                            'name': 'STATUS_FINAL' if complete else 'STATUS_IN_PROGRESS'}},
        'competitors': [{'homeAway': side, 'team': {'id': context[side + '_id'], 'displayName': context[side + '_team']},
                         'score': str(total if side == 'home' else 0)} for side in ('home', 'away')]}]},
        'gameInfo': {'venue': {'id': '3793'}}}


class Session:
    def __init__(self, observations, complete=True, total=38, status=200):
        self.observations = {o['game_id']: o for o in observations}
        self.complete, self.total, self.status = complete, total, status
        self.calls = []

    def get(self, url, params, **kwargs):
        self.calls.append((url, deepcopy(params), kwargs))
        assert url == module.SUMMARY
        assert kwargs['allow_redirects'] is False
        response = requests.Response()
        response.status_code = self.status
        response._content = json.dumps(payload(self.observations[params['event']], complete=self.complete, total=self.total)).encode()
        response.headers['Content-Type'] = 'application/json'
        return response


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.obs = observation()

    def client(self, observations=None, **kwargs):
        session = Session(observations or [self.obs], **kwargs)
        client = archive.ArchiveClient(self.root / 'model', archive=self.root / module.BASE / 'http', session=session)
        return client, session

    def collect(self, observations=None, now=NOW, client=None, **kwargs):
        client = client or self.client(observations)[0]
        # Same fixed actual clock for all fake responses within this test call.
        with patch.object(archive, 'timestamp', return_value=module._iso(now)), \
             patch.object(module, 'timestamp', return_value=module._iso(now)):
            return module.collect_labels(self.root, [self.obs] if observations is None else observations,
                                         [], envelope=lambda path: None, now=now, client=client, **kwargs)

    def test_zero_observations_and_pre_six_hour_or_integer_games_make_no_http(self):
        for observations, when in [([], NOW), ([self.obs], KICKOFF + timedelta(hours=6) - timedelta(microseconds=1)),
                                   ([observation(line=54)], NOW)]:
            with self.subTest(observations=len(observations), when=when):
                client, session = self.client(observations or [self.obs])
                result = self.collect(observations, now=when, client=client)
                self.assertEqual(result['counts']['final_http_calls'], 0)
                self.assertFalse(session.calls)

    def test_exact_six_hour_gate_and_original_label_receipt_are_preserved(self):
        when = KICKOFF + timedelta(hours=6)
        result = self.collect(now=when)
        self.assertEqual(result['counts']['final_http_calls'], 1)
        target = next(r for r in result['records'] if r['target_kind'] == 'final_total')
        self.assertEqual(target['target_available_at'], module._iso(when))
        self.assertEqual(target['final_total'], 38)
        path = self.root / module.BASE / 'labels' / (module.digest_json(target) + '.json')
        self.assertEqual(json.loads(path.read_text()), target)
        self.assertTrue((self.root / module.BASE / 'observations' / (self.obs['observation_id'] + '.json')).exists())
        original = module.load_study_envelope(self.root, target['receipt_path'])
        self.assertEqual(original['receipt']['received_at'], target['target_available_at'])

    def test_pending_polls_wait_six_hours_and_attempts_are_immutable(self):
        client, session = self.client(complete=False)
        first = self.collect(client=client)
        self.assertEqual(first['attempts'][0]['status'], 'pending_final')
        self.assertEqual(first['status'], 'ok')
        self.assertFalse(first['errors'])
        self.assertEqual(first['counts']['final_targets_pending'], 1)
        paths = list((self.root / module.BASE / 'label_attempts').glob('*.json'))
        before = {p: p.read_bytes() for p in paths}
        self.collect(now=NOW + timedelta(hours=6) - timedelta(microseconds=1), client=client)
        self.assertEqual(len(session.calls), 1)
        self.collect(now=NOW + timedelta(hours=6), client=client)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(len(list((self.root / module.BASE / 'label_attempts').glob('*.json'))), 2)
        self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))
        self.assertFalse(list((self.root / module.BASE / 'labels').glob('*.json')))

    def test_final_corrections_wait_twenty_four_hours_and_append(self):
        first = self.collect()
        original = next(r for r in first['records'] if r['target_kind'] == 'final_total')
        client, session = self.client(total=70)
        self.collect(now=NOW + timedelta(hours=24) - timedelta(microseconds=1), client=client)
        self.assertFalse(session.calls)
        self.collect(now=NOW + timedelta(hours=24), client=client)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(len(list((self.root / module.BASE / 'labels').glob('*.json'))), 2)
        early = module.load_latest_labels(self.root, cutoff=NOW + timedelta(hours=12))
        self.assertEqual(early['final_total'][self.obs['observation_id']], original)
        later = module.load_latest_labels(self.root, cutoff=NOW + timedelta(hours=25))
        self.assertEqual(later['final_total'][self.obs['observation_id']]['final_total'], 70)

    def test_strict_cutoff_differs_from_inclusive_scoring(self):
        self.collect()
        strict = module.load_latest_labels(self.root, cutoff=NOW)
        inclusive = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertEqual(strict['final_total'], {})
        self.assertEqual(inclusive['final_total'][self.obs['observation_id']]['final_total'], 38)

    def test_oldest_last_poll_queue_caps_twenty_four_without_starvation(self):
        observations = [observation(gid) for gid in range(1, 27)]
        client, session = self.client(observations, complete=False)
        first = self.collect(list(reversed(observations)), client=client)
        self.assertEqual(first['counts']['final_http_calls'], 24)
        self.assertEqual(first['counts']['final_deferred_by_cap'], 2)
        self.assertEqual([p['event'] for _, p, _ in session.calls], [str(i) for i in range(1, 25)])
        second = self.collect(observations, now=NOW + timedelta(seconds=1), client=client)
        self.assertEqual(second['counts']['final_http_calls'], 2)
        self.assertEqual([p['event'] for _, p, _ in session.calls[-2:]], ['25', '26'])

    def test_no_final_http_at_or_after_report_cutoff(self):
        client, session = self.client()
        for when in (module.REPORT_AT, module.REPORT_AT + timedelta(seconds=1)):
            result = self.collect(now=when, client=client)
            self.assertEqual(result['counts']['final_http_calls'], 0)
        self.assertFalse(session.calls)

    def test_crossing_deadline_stops_before_next_game(self):
        observations = [observation(1), observation(2)]
        client, session = self.client(observations)
        before = module.REPORT_AT - timedelta(seconds=1)
        with patch.object(module, 'timestamp', return_value=module._iso(before)), \
             patch.object(archive, 'timestamp', side_effect=[module._iso(before), module._iso(module.REPORT_AT)]):
            result = module.collect_labels(self.root, observations, [], envelope=lambda path: None,
                                           now=before, client=client)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(any(item['reason'] == 'final_poll_deadline_reached' for item in result['unavailable_targets']))

    def test_source_access_failure_is_archived_and_not_retried(self):
        observations = [observation(1), observation(2)]
        client, session = self.client(observations, status=429)
        first = self.collect(observations, client=client)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(first['attempts'][0]['status'], 'request_failed')
        self.assertEqual(first['attempts'][0]['status_code'], 429)
        self.assertFalse(list((self.root / module.BASE / 'labels').glob('*.json')))

    def test_same_timestamp_conflicting_finals_are_unavailable(self):
        self.collect()
        client, _ = self.client(total=70)
        with patch.object(archive, 'timestamp', return_value=module._iso(NOW)):
            response = client.fetch(module.SUMMARY, {'event': '1'}, purpose='synthetic-correction')
        label = module.dataset.final_label(self.obs, response['payload'], response['receipt'])
        module._store_label(self.root, label)
        latest = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertEqual(latest['final_total'], {})
        self.assertEqual(latest['errors'][0]['reason'], 'ambiguous_same_timestamp_labels')

    def test_equivalent_same_timestamp_receipts_are_not_conflicting_labels(self):
        self.collect()
        client, _ = self.client()
        with patch.object(archive, 'timestamp', return_value=module._iso(NOW)):
            response = client.fetch(module.SUMMARY, {'event': '1'}, purpose='separate-same-value-receipt')
        module._store_label(self.root, module.dataset.final_label(self.obs, response['payload'], response['receipt']))
        latest = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertFalse(latest['errors'])
        self.assertEqual(latest['final_total'][self.obs['observation_id']]['final_total'], 38)

    def test_corrupt_original_body_or_label_digest_fails_closed(self):
        result = self.collect()
        target = next(r for r in result['records'] if r['target_kind'] == 'final_total')
        receipt = module.load_study_envelope(self.root, target['receipt_path'])['receipt']
        path = self.root / 'model' / receipt['body_path']
        path.write_bytes(gzip.compress(b'{}'))
        latest = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertEqual(latest['final_total'], {})
        self.assertEqual(latest['errors'][0]['reason'], 'invalid_label_archive')

    def test_receipt_size_and_namespace_are_verified(self):
        result = self.collect()
        target = next(r for r in result['records'] if r['target_kind'] == 'final_total')
        receipt = module.load_study_envelope(self.root, target['receipt_path'])['receipt']
        changed = deepcopy(receipt)
        changed.pop('receipt_path')
        changed['body_size_bytes'] += 1
        relative = 'data/runtime/weather_revision_study/http/receipts/' + module.digest_json(changed) + '.json'
        changed['receipt_path'] = relative
        archive.immutable_json(self.root / 'model' / relative, changed)
        with self.assertRaisesRegex(ValueError, 'hash_or_size'):
            module.load_study_envelope(self.root, relative)
        with self.assertRaisesRegex(ValueError, 'namespace'):
            module.load_study_envelope(self.root, '../not-an-archive.json')

    def test_corrupt_poll_attempt_prevents_unbounded_replication(self):
        client, session = self.client(complete=False)
        self.collect(client=client)
        path = next((self.root / module.BASE / 'label_attempts').glob('*.json'))
        value = json.loads(path.read_text())
        value['received_at'] = module._iso(NOW - timedelta(days=1))
        path.write_text(json.dumps(value))
        result = self.collect(now=NOW + timedelta(hours=7), client=client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(session.calls), 1)

    def test_complete_enumeration_is_passed_to_pure_movement_helper(self):
        sentinel_runs = [{'synthetic': 'ordered archive inventory'}]
        with patch.object(module.dataset, 'movement_label', side_effect=ValueError('missing target')) as movement:
            module.collect_labels(self.root, [self.obs], sentinel_runs, envelope=lambda path: None,
                                  now=KICKOFF, complete_enumeration=False)
        self.assertIs(movement.call_args.args[1], sentinel_runs)
        self.assertFalse(movement.call_args.kwargs['complete_enumeration'])

    def test_conflicting_game_designations_fail_before_network(self):
        other = observation(line=55.5)
        client, session = self.client()
        result = self.collect([self.obs, other], client=client)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(session.calls)

    def test_real_pure_movement_label_persists_and_reloads_from_synthetic_capture_bodies(self):
        # Reuse the model adapter's complete synthetic HTTP/source fixture;
        # none of the repository's actual observations or outcomes are loaded.
        tests = Path(__file__).resolve().parents[2] / 'model/tests'
        sys.path.insert(0, str(tests))
        try:
            import test_weather_revision_dataset as fixture
            import pytest
            with pytest.MonkeyPatch.context() as monkey:
                environment = fixture.environment.__wrapped__(self.root, monkey)
                data = fixture.data.__wrapped__(environment, monkey)
                repository = environment[0].parent
                now = fixture.T0 + timedelta(hours=13)
                result = module.collect_labels(repository, [data['observation']], data['runs'],
                                               envelope=data['envelope'], now=now)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['counts']['final_http_calls'], 0)
                self.assertEqual(result['counts']['movement_labels_available'], 1)
                self.assertEqual(result['counts']['labels_written'], 1)
                latest = module.load_latest_labels(repository, cutoff=now)
                self.assertFalse(latest['errors'])
                target = latest['market_movement'][data['observation']['observation_id']]
                self.assertEqual(target['movement_points'], 1)
                again = module.collect_labels(repository, [data['observation']], data['runs'],
                                              envelope=data['envelope'], now=now)
                self.assertEqual(again['counts']['labels_written'], 0)
        finally:
            sys.path.remove(str(tests))

    def test_hash_valid_but_semantically_false_label_is_rejected(self):
        result = self.collect()
        label = deepcopy(next(r for r in result['records'] if r['target_kind'] == 'final_total'))
        label['final_total'] = 70
        label['label_under'] = 0
        archive.immutable_json(self.root / module.BASE / 'labels' / (module.digest_json(label) + '.json'), label)
        latest = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertEqual(latest['final_total'], {})
        self.assertEqual(latest['errors'][0]['reason'], 'invalid_label_archive')

    def test_observation_corruption_is_detected_before_reusing_labels(self):
        self.collect()
        path = self.root / module.BASE / 'observations' / (self.obs['observation_id'] + '.json')
        changed = json.loads(path.read_text())
        changed['reference_line'] = 99.5
        path.write_text(json.dumps(changed))
        latest = module.load_latest_labels(self.root, cutoff=NOW, inclusive=True)
        self.assertEqual(latest['final_total'], {})
        self.assertEqual(latest['errors'][0]['reason'], 'invalid_label_archive')


if __name__ == '__main__':
    unittest.main()
