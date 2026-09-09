"""Original-response reconstruction and separate labels, all synthetic/offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from ncaaf_model import revision_archive as archive
from ncaaf_model import weather_revision_collector as collector
from ncaaf_model import weather_revision_dataset as dataset
from ncaaf_model.weather_revision_models import ALL_FEATURES, BASE_FEATURES, REVISION_FEATURES
from ncaaf_model.weather_revision_weather import SINGLE_URL
from test_weather_revision_collector import SyntheticSession, environment, event, summary


T0 = datetime(2026, 9, 10, 7, 17, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 12, 8, tzinfo=timezone.utc)


class WeatherSession(SyntheticSession):
    def __init__(self, root, games, index, when):
        super().__init__(root, games)
        self.index = index
        for source in self.feed.values():
            for book, markets in source['bookmakers'].items():
                markets[0]['updatedAt'] = (when - timedelta(minutes=10)).isoformat()
                markets[0]['odds'] = [{'hdp': 54.5 + index + (book == 'FanDuel'),
                                     'over': 1.92 + index / 100, 'under': 1.88 + index / 100}]

    def get(self, url, params, **kwargs):
        response = super().get(url, params, **kwargs)
        if url == SINGLE_URL:
            payload = json.loads(response.content)
            n = len(payload['hourly']['time'])
            payload['hourly']['temperature_2m'] = [70. - self.index * 5] * n
            payload['hourly']['relative_humidity_2m'] = [60. + self.index * 10] * n
            payload['hourly']['wind_speed_10m'] = [6. + self.index * 2 + (i % 4) * 2 for i in range(n)]
            response.content = json.dumps(payload, allow_nan=False).encode()
        return response


def capture(root, clock, index, when=None):
    when = when or T0 + timedelta(hours=6 * index)
    clock.value = when
    session = WeatherSession(root, [event(kickoff=KICKOFF)], index, when)
    client = archive.ArchiveClient(root, session=session)
    manifest = collector.collect(root, now=when, client=client)
    assert manifest['status'] == 'ok'
    path = root / 'data/runtime/weather_revisions/runs' / f"{manifest['run_id']}-1.json"
    cpath = root / manifest['cohort_path']
    return {'manifest': manifest, 'cohort': json.loads(cpath.read_text()),
            'rows': {r['game_id']: r for r in manifest['rows']},
            'path': 'model/' + path.relative_to(root).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'cohort_sha256': hashlib.sha256(cpath.read_bytes()).hexdigest(),
            # Synthetic source-integrity boundary: real runner uses inventory verifier.
            'integrity': {'verified': True}}, session


@pytest.fixture
def data(environment, monkeypatch):
    root, clock = environment
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    runs = [capture(root, clock, i)[0] for i in range(3)]
    old, new = runs[:2]
    row0, row1 = old['rows']['401'], new['rows']['401']
    q0 = next(q for q in row0['quotes'] if q['sportsbook'] == 'draftkings')
    dk = next(q for q in row1['quotes'] if q['sportsbook'] == 'draftkings')
    fd = next(q for q in row1['quotes'] if q['sportsbook'] == 'fanduel')
    decision = {'game_id': '401', 'kickoff': row1['kickoff'],
                'run_id': new['manifest']['run_id'], 'run_attempt': '1',
                'preceding_run_id': old['manifest']['run_id'],
                'capture_started_at': new['manifest']['capture_started_at'],
                'context_id': row1['context_id'], 'source_receipt_verified_input_eligible': True,
                'decision_received_at': max(dk['observed_at'], fd['observed_at']),
                'quote_ids': {'t0_draftkings': q0['quote_id'], 't1_draftkings': dk['quote_id'], 't1_fanduel': fd['quote_id']},
                'weather_receipts': [row0['single_run']['receipt_path'], row1['single_run']['receipt_path']]}
    envelope = lambda path: archive.load_envelope(root, path)
    observation = dataset.build_observation(decision, old, new, envelope=envelope)
    return {'root': root, 'clock': clock, 'runs': runs, 'decision': decision,
            'envelope': envelope, 'observation': observation}


def rebuild(data, runs=None, decision=None, envelope=None):
    return dataset.build_observation(decision or data['decision'], *(runs or data['runs'])[:2],
                                     envelope=envelope or data['envelope'])


def test_exact_eleven_features_prices_and_provenance(data):
    obs = data['observation']
    assert tuple(obs['features']) == ALL_FEATURES == BASE_FEATURES + REVISION_FEATURES
    assert len(obs['features']) == 11
    expected = {'market_total': 55.5, 'wind_mph': 11., 'temperature_f': 65.,
                'relative_humidity_percent': 70., 'market_total_change': 1.,
                'peer_total_difference': 1., 'wind_revision_mph': 2.,
                'temperature_revision_f': -5., 'humidity_revision_percent': 10.}
    for key, value in expected.items():
        assert obs['features'][key] == value
    assert obs['features']['hours_to_kickoff'] == pytest.approx((KICKOFF - dataset._time(obs['decision_at'])).total_seconds() / 3600)
    assert obs['q_under'] == pytest.approx(1.93 / (1.93 + 1.89))
    import math
    q0 = 1.92 / (1.92 + 1.88)
    q1 = obs['q_under']
    assert obs['features']['price_balance_logit_change'] == pytest.approx(math.log(q1 / (1 - q1)) - math.log(q0 / (1 - q0)))
    assert obs['reference_line'] == 55.5
    assert len(obs['observed_offers']) == 2
    assert {q['line'] for q in obs['observed_offers']} == {55.5, 56.5}
    assert obs['t0']['sha256'] == data['runs'][0]['sha256']
    assert obs['t1']['path'] == data['runs'][1]['path']
    assert obs['quote_ids'] == data['decision']['quote_ids']
    assert 'final_total' not in obs and 'label_under' not in obs


@pytest.mark.parametrize('kind', ['source', 'designation', 'wrong_run', 'context', 'mean', 'hour', 'temperature', 'quote', 'line_duplicate', 'manual'])
def test_corrupt_or_unverified_inputs_fail_without_imputation(data, kind):
    runs, decision = deepcopy(data['runs']), deepcopy(data['decision'])
    row = runs[1]['rows']['401']
    if kind == 'source': runs[0]['integrity']['verified'] = False
    elif kind == 'designation': decision['source_receipt_verified_input_eligible'] = False
    elif kind == 'wrong_run': decision['preceding_run_id'] = 'different'
    elif kind == 'context': row['context']['venue_id'] = '999'
    elif kind == 'mean': row['single_run']['measurement']['wind_mph_four_hour_mean'] += 1
    elif kind == 'hour': row['single_run']['measurement']['wind_mph_by_hour'][0] += 1
    elif kind == 'temperature': row['single_run']['measurement']['temperature_f'] = 161
    elif kind == 'quote': row['quotes'][0]['under_decimal_odds'] = 5
    elif kind == 'line_duplicate': row['quotes'].append(deepcopy(row['quotes'][0]))
    elif kind == 'manual': runs[1]['manifest']['trigger'] = 'workflow_dispatch'
    with pytest.raises(ValueError):
        rebuild(data, runs, decision)


@pytest.mark.parametrize('variable,value', [('temperature_2m', 161.), ('relative_humidity_2m', -1.), ('wind_speed_10m', 251.), ('wind_speed_10m', None)])
def test_invalid_original_forecast_values_fail_strict_parser(data, variable, value):
    original = data['envelope']
    target = data['runs'][1]['rows']['401']['single_run']['receipt_path']

    def corrupt(path):
        result = deepcopy(original(path))
        if path == target:
            result['payload']['hourly'][variable] = [value] * 192
        return result

    with pytest.raises(ValueError):
        rebuild(data, envelope=corrupt)


def test_decision_uses_later_book_receipt_and_never_mutates_inputs(data):
    runs, decision = deepcopy(data['runs']), deepcopy(data['decision'])
    before = deepcopy(runs)
    rebuilt = rebuild(data, runs, decision)
    assert runs == before
    assert rebuilt['decision_at'] == max(q['observed_at'] for q in rebuilt['observed_offers'])
    # A designation cannot move the information cutoff to an earlier observation.
    decision['decision_received_at'] = runs[1]['rows']['401']['single_run']['measurement']['received_at']
    with pytest.raises(ValueError, match='cutoff'):
        rebuild(data, runs, decision)


def test_different_book_receipts_use_maximum_not_first_book(data):
    runs, decision = deepcopy(data['runs']), deepcopy(data['decision'])
    row = runs[1]['rows']['401']
    old_fd = next(q for q in row['quotes'] if q['sportsbook'] == 'fanduel')
    response = deepcopy(data['envelope'](old_fd['receipt_path']))
    receipt = response['receipt']
    receipt['requested_at'] = dataset._stamp(dataset._time(receipt['requested_at']) + timedelta(seconds=5))
    receipt['received_at'] = dataset._stamp(dataset._time(receipt['received_at']) + timedelta(seconds=5))
    receipt['receipt_path'] = 'data/runtime/weather_revisions/receipts/separate-fanduel.json'
    rebuilt, _ = collector.parse_quote_pairs(response['payload'], {old_fd['provider_event_id']: row}, receipt)
    new_fd = next(q for q in rebuilt if q['sportsbook'] == 'fanduel')
    row['quotes'] = [new_fd if q['sportsbook'] == 'fanduel' else q for q in row['quotes']]
    for pair in row['pairs']:
        if pair['sportsbook'] == 'fanduel':
            pair.update(quote_id=new_fd['quote_id'], quote_receipt=new_fd['receipt_path'],
                        weather_to_quote_seconds=pair['weather_to_quote_seconds'] + 5)
    runs[1]['manifest']['capture_completed_at'] = dataset._stamp(dataset._time(receipt['received_at']) + timedelta(seconds=1))
    decision['decision_received_at'] = receipt['received_at']
    decision['quote_ids']['t1_fanduel'] = new_fd['quote_id']
    envelope = lambda path: response if path == receipt['receipt_path'] else data['envelope'](path)
    obs = rebuild(data, runs, decision, envelope)
    assert obs['decision_at'] == receipt['received_at']
    assert obs['features']['hours_to_kickoff'] < data['observation']['features']['hours_to_kickoff']


def final_response(data, *, home=21, away=17, complete=True):
    payload = summary(event(kickoff=KICKOFF))
    c = payload['header']['competitions'][0]
    c['status']['type'] = {'state': 'post' if complete else 'in', 'completed': complete, 'name': 'STATUS_FINAL' if complete else 'STATUS_IN_PROGRESS'}
    for p in c['competitors']:
        p['score'] = str(home if p['homeAway'] == 'home' else away)
    when = KICKOFF + timedelta(hours=5)
    receipt = {'schema_version': 'raw-http-receipt-v1', 'status_code': 200, 'transport_error': None,
               'requested_at': when.isoformat(), 'received_at': (when + timedelta(seconds=1)).isoformat(),
               'receipt_path': 'data/runtime/weather_revisions/receipts/synthetic-final.json',
               'body_path': 'data/runtime/weather_revisions/bodies/synthetic-final.body.gz',
               'body_sha256': hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
               'request': {'url': collector.SUMMARY, 'params': {'event': '401'}}}
    return payload, receipt


def test_final_labels_are_separate_with_original_receipt_availability(data):
    obs = data['observation']
    original = deepcopy(obs)
    payload, receipt = final_response(data)
    label = dataset.final_label(obs, payload, receipt)
    assert label['final_total'] == 38 and label['label_under'] == 1
    assert label['target_available_at'] == receipt['received_at']
    assert label['observation_id'] == obs['observation_id']
    assert label['game_id'] == obs['game_id']
    opposite = dataset.final_label(obs, *final_response(data, home=45, away=35))
    assert opposite['label_under'] == 0
    assert obs == original
    # Serialized mappings may be sorted by the immutable ledger writer.
    assert dataset.final_label(json.loads(json.dumps(obs, sort_keys=True)), payload, receipt) == label


class UnreadableScore(dict):
    def get(self, key, *args):
        if key == 'score':
            raise AssertionError('Score accessed before completion and identity validation')
        return super().get(key, *args)


@pytest.mark.parametrize('kind', ['incomplete', 'completed_false', 'canceled', 'wrong_game', 'wrong_team', 'wrong_source', 'early_receipt'])
def test_final_guard_precedes_every_score_read(data, kind):
    payload, receipt = final_response(data)
    c = payload['header']['competitions'][0]
    c['competitors'] = [UnreadableScore(p) for p in c['competitors']]
    if kind == 'incomplete': c['status']['type']['state'] = 'in'
    elif kind == 'completed_false': c['status']['type']['completed'] = False
    elif kind == 'canceled': c['status']['type']['name'] = 'STATUS_CANCELED'
    elif kind == 'wrong_game': payload['header']['id'] = '402'
    elif kind == 'wrong_team': c['competitors'][0]['team']['id'] = '42'
    elif kind == 'wrong_source': receipt['request']['url'] = 'https://example.invalid/final'
    else: receipt['requested_at'] = T0.isoformat()
    with pytest.raises(ValueError):
        dataset.final_label(data['observation'], payload, receipt)


@pytest.mark.parametrize('value', [True, -1, '1.5', None, float('nan')])
def test_invalid_final_score_unavailable(data, value):
    payload, receipt = final_response(data)
    payload['header']['competitions'][0]['competitors'][0]['score'] = value
    with pytest.raises(ValueError, match='invalid_final_score'):
        dataset.final_label(data['observation'], payload, receipt)


def test_post_lock_context_changes_are_flagged_without_dropping_final(data):
    payload, receipt = final_response(data, home=42, away=38)
    c = payload['header']['competitions'][0]
    c['date'] = (KICKOFF - timedelta(hours=2)).isoformat()
    c['neutralSite'] = True
    payload['gameInfo']['venue'] = {'id': '999', 'indoor': True}
    target = dataset.final_label(data['observation'], payload, receipt)
    assert target['final_total'] == 80 and target['label_under'] == 0
    assert target['reported_kickoff'] == dataset._stamp(KICKOFF - timedelta(hours=2))
    assert set(target['context_flags']) == {'kickoff_changed', 'venue_changed', 'neutral_site_changed', 'roof_changed'}
    assert target['context_changed'] is True


def test_rescheduled_earlier_final_retains_actual_availability_before_original_kickoff(data):
    payload, receipt = final_response(data)
    actual = KICKOFF - timedelta(hours=10)
    payload['header']['competitions'][0]['date'] = actual.isoformat()
    receipt['requested_at'] = (actual + timedelta(hours=4)).isoformat()
    receipt['received_at'] = (actual + timedelta(hours=4, seconds=1)).isoformat()
    target = dataset.final_label(data['observation'], payload, receipt)
    assert dataset._time(target['target_available_at']) < KICKOFF
    assert target['context_flags'] == ['kickoff_changed']


def test_finite_integral_numeric_final_is_supported(data):
    payload, receipt = final_response(data)
    payload['header']['competitions'][0]['competitors'][0]['score'] = 21.0
    assert dataset.final_label(data['observation'], payload, receipt)['final_total'] == 38


@pytest.mark.parametrize('name', [None, 'STATUS_FINAL_3OT'])
def test_final_completion_contract_does_not_depend_on_optional_status_name(data, name):
    payload, receipt = final_response(data)
    status = payload['header']['competitions'][0]['status']['type']
    if name is None:
        status.pop('name')
    else:
        status['name'] = name
    assert dataset.final_label(data['observation'], payload, receipt)['final_total'] == 38


def test_movement_uses_immediate_next_unique_dk_quote_without_any_weather(data):
    runs, obs = deepcopy(data['runs']), deepcopy(data['observation'])
    row = runs[2]['rows']['401']
    for key in ('single_run', 'previous_day2', 'pairs', 'context_recheck_receipt', 'context_recheck_ok', 'context_recheck_received_at'):
        row.pop(key, None)
    row['quotes'] = [q for q in row['quotes'] if q['sportsbook'] == 'draftkings']
    accessed = []

    def envelope(path):
        accessed.append(path)
        return data['envelope'](path)

    target = dataset.movement_label(obs, runs, envelope=envelope)
    assert target['movement_points'] == 1 and target['target_line'] == 56.5
    assert target['target_available_at'] == row['quotes'][0]['observed_at']
    assert target['observation_id'] == obs['observation_id']
    assert data['runs'][2]['rows']['401']['single_run']['receipt_path'] not in accessed
    assert obs == data['observation']


@pytest.mark.parametrize('kind', ['missing_quote', 'duplicate_quote', 'integrity', 'context', 'missing_row'])
def test_missing_next_target_never_substitutes_later_valid_capture(data, kind):
    runs = deepcopy(data['runs'])
    later = deepcopy(runs[2])
    later['manifest']['run_id'] = 'later-success'
    later['manifest']['capture_started_at'] = (T0 + timedelta(hours=18)).isoformat()
    later['manifest']['capture_completed_at'] = (T0 + timedelta(hours=18, minutes=1)).isoformat()
    row = runs[2]['rows']['401']
    if kind == 'missing_quote': row['quotes'] = []
    elif kind == 'duplicate_quote': row['quotes'].append(deepcopy(row['quotes'][0]))
    elif kind == 'integrity': runs[2]['integrity']['verified'] = False
    elif kind == 'context': row['context']['venue_id'] = '999'
    else: runs[2]['rows'] = {}
    with pytest.raises(ValueError):
        dataset.movement_label(data['observation'], [*runs, later], envelope=data['envelope'])


def test_movement_does_not_replace_missing_scheduled_target_with_manual_capture(data):
    runs = deepcopy(data['runs'])
    runs[2]['manifest']['trigger'] = 'workflow_dispatch'
    with pytest.raises(ValueError, match='next_scheduled_capture_unavailable'):
        dataset.movement_label(data['observation'], runs, envelope=data['envelope'])
    with pytest.raises(ValueError, match='complete_archive_enumeration_required'):
        dataset.movement_label(data['observation'], data['runs'], envelope=data['envelope'], complete_enumeration=False)


@pytest.mark.parametrize('hours,valid', [(3.999, False), (4., True), (8., True), (8.001, False)])
def test_movement_target_receipt_horizon_is_inclusive_and_exact(data, hours, valid):
    runs = deepcopy(data['runs'])
    row = runs[2]['rows']['401']
    old_quote = next(q for q in row['quotes'] if q['sportsbook'] == 'draftkings')
    response = deepcopy(data['envelope'](old_quote['receipt_path']))
    r = response['receipt']
    when = dataset._time(data['observation']['decision_at']) + timedelta(hours=hours)
    r['received_at'] = dataset._stamp(when)
    r['requested_at'] = dataset._stamp(when - timedelta(seconds=.1))
    for source in response['payload']:
        for markets in source['bookmakers'].values():
            for market in markets:
                market['updatedAt'] = dataset._stamp(when - timedelta(minutes=10))
    # Target context/source fixture clocks precede any of the tested receipts.
    start = dataset._time(runs[2]['manifest']['capture_started_at'])
    shift = timedelta(hours=-3)
    replacements = {r['receipt_path']: response}
    for key in ('summary_receipt', 'roof_receipt'):
        e = deepcopy(data['envelope'](row[key]))
        for field in ('requested_at', 'received_at'):
            e['receipt'][field] = dataset._stamp(dataset._time(e['receipt'][field]) + shift)
        replacements[row[key]] = e
    runs[2]['manifest']['capture_started_at'] = dataset._stamp(start + shift)
    runs[2]['manifest']['capture_completed_at'] = dataset._stamp(when + timedelta(seconds=1))
    rebuilt, _ = collector.parse_quote_pairs(response['payload'], {old_quote['provider_event_id']: row}, r)
    row['quotes'] = [q for q in rebuilt if q['sportsbook'] == 'draftkings']
    envelope = lambda path: replacements[path] if path in replacements else data['envelope'](path)
    if valid:
        result = dataset.movement_label(data['observation'], runs, envelope=envelope)
        assert result['decision_to_target_hours'] == hours
    else:
        with pytest.raises(ValueError, match='outside_4_8_hours'):
            dataset.movement_label(data['observation'], runs, envelope=envelope)


def test_integer_reference_is_movement_only_and_final_labels_do_not_modify_it(data):
    runs, decision = deepcopy(data['runs']), deepcopy(data['decision'])
    row = runs[1]['rows']['401']
    quote = row['quotes'][0]
    response = deepcopy(data['envelope'](quote['receipt_path']))
    for source in response['payload']:
        source['bookmakers']['DraftKings'][0]['odds'][0]['hdp'] = 55
    rebuilt, _ = collector.parse_quote_pairs(response['payload'], {quote['provider_event_id']: row}, response['receipt'])
    previous_ids = {q['sportsbook']: q['quote_id'] for q in row['quotes']}
    row['quotes'] = rebuilt
    for q in rebuilt:
        decision['quote_ids']['t1_' + q['sportsbook']] = q['quote_id']
        for pair in row['pairs']:
            if pair['quote_id'] == previous_ids[q['sportsbook']]:
                pair['quote_id'] = q['quote_id']
    envelope = lambda path: response if path == quote['receipt_path'] else data['envelope'](path)
    observation = rebuild(data, runs, decision, envelope)
    assert observation['probability_input_eligible'] is False and observation['movement_input_eligible'] is True
    assert observation['reference_line'] == 55
    with pytest.raises(ValueError, match='half_point_probability_contract_required'):
        dataset.final_label(observation, *final_response(data))
