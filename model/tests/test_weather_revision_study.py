"""Synthetic prospective timing/selection tests; no real game labels."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ncaaf_model import weather_revision_models as models
from ncaaf_model import weather_revision_study as study

PROTOCOL = 'a' * 64
CUTOFF = '2025-09-22T04:00:00Z'
NOW = '2025-09-26T10:00:10Z'


def observation(gid, kickoff, decision):
    f = {name: 0. for name in models.ALL_FEATURES}
    f.update(market_total=50.5, hours_to_kickoff=36., wind_mph=7., temperature_f=70., relative_humidity_percent=60.)
    return {'observation_id': 'observation-' + str(gid), 'game_id': str(gid), 'context_id': 'context-' + str(gid),
            'kickoff': study.iso(kickoff), 'decision_at': study.iso(decision), 'reference_line': 50.5,
            'q_under': .5, 'features': f, 'observed_offers': [
                {'sportsbook': book, 'line': 50.5, 'over_decimal_odds': 1.91, 'under_decimal_odds': 1.95,
                 'observed_at': study.iso(decision), 'quote_id': book + '-' + str(gid), 'receipt_path': book + '.json'}
                for book in ('draftkings', 'fanduel')]}


def data(kind='final_total'):
    rows, labels = [], {}
    for i in range(60):
        kickoff = datetime(2025, 9, 6, 22, tzinfo=timezone.utc) + timedelta(weeks=i // 30, minutes=i % 30)
        o = observation(i + 1, kickoff, kickoff - timedelta(hours=36))
        rows.append(o)
        available = kickoff + timedelta(hours=4) if kind == 'final_total' else kickoff - timedelta(hours=30)
        labels[o['observation_id']] = {'observation_id': o['observation_id'], 'game_id': o['game_id'],
            'target_kind': kind, 'target_available_at': study.iso(available), 'reported_kickoff': o['kickoff'],
            'label_under': 1, 'final_total': 40, 'movement_points': -2.}
    test = observation(100, '2025-09-27T22:00:00Z', '2025-09-26T10:00:00Z')
    return rows, labels, test


def artifact(kind='final_total'):
    rows, labels, test = data(kind)
    return study.fit_pair(rows, labels, cutoff=CUTOFF, target_kind=kind, protocol_sha256=PROTOCOL), test


def predict(a, o, **changes):
    args = {'available_at': '2025-09-22T04:01:00Z', 'recorded_at': NOW, 'protocol_sha256': PROTOCOL}
    args.update(changes)
    return study.make_prediction(o, a, **args)


def test_weekly_cutoff_obeys_eastern_dst_and_completed_calendar_weeks():
    assert study.iso(study.weekly_cutoff('2025-09-22T03:59:59Z')) == '2025-09-15T04:00:00Z'
    assert study.iso(study.weekly_cutoff('2025-09-22T04:00:00Z')) == CUTOFF
    assert study.iso(study.weekly_cutoff('2025-11-03T05:00:00Z')) == '2025-11-03T05:00:00Z'


def test_training_uses_sixty_unique_prior_games_and_excludes_late_labels():
    rows, labels, _ = data()
    before = deepcopy((rows, labels))
    a = study.fit_pair(rows, labels, cutoff=CUTOFF, target_kind='final_total', protocol_sha256=PROTOCOL)
    assert a['status'] == 'fitted' and a['games'] == 60 and a['completed_weeks'] == 2
    assert a['models']['reference']['training_rows'] == a['models']['revision']['training_rows'] == 60
    assert (rows, labels) == before
    labels[rows[0]['observation_id']]['target_available_at'] = CUTOFF
    b = study.fit_pair(rows, labels, cutoff=CUTOFF, target_kind='final_total', protocol_sha256=PROTOCOL)
    assert b['status'] == 'insufficient_training' and b['games'] == 59
    with pytest.raises(ValueError, match='Repeated'):
        study.training_sample(rows + [rows[1]], labels, cutoff=CUTOFF, target_kind='final_total')


def test_label_cannot_be_assigned_to_another_game():
    rows, labels, _ = data()
    labels[rows[0]['observation_id']]['game_id'] = '999'
    with pytest.raises(ValueError, match='different'):
        study.training_sample(rows, labels, cutoff=CUTOFF, target_kind='final_total')


@pytest.mark.parametrize('kind', ['final_total', 'market_movement'])
def test_model_round_trip_preserves_predictions_and_zero_variance_movement(kind):
    a, o = artifact(kind)
    p = predict(a, o)
    assert p['artifact_sha256'] == study.digest(a)
    if kind == 'market_movement':
        assert p['prediction'] == {'reference': -2., 'revision': -2., 'zero_movement': 0.}
        assert study.paper_selection(o, p) is None
    else:
        assert p['prediction']['revision'] > .5
        assert p['prediction']['price_reference'] == .5
        for name, value in a['models'].items():
            round_trip = study.serialize_model(study.deserialize_model(value))
            assert round_trip == value


@pytest.mark.parametrize('change', [
    {'available_at':'2025-09-26T10:00:01Z'},
    {'recorded_at':'2025-09-26T10:02:00.000001Z'},
    {'recorded_at':'2025-09-26T09:59:59Z'},
    {'protocol_sha256':'b'*64},
])
def test_no_backdated_model_stale_price_or_protocol_substitution(change):
    a, o = artifact()
    with pytest.raises(ValueError):
        predict(a, o, **change)


def test_inference_latency_is_checked_after_computing_the_prediction():
    a, o = artifact()
    times = iter(['2025-09-26T10:01:59Z', '2025-09-26T10:02:01Z'])
    with pytest.raises(ValueError, match='during inference'):
        predict(a, o, recorded_at=lambda: next(times))
    p = predict(a, o, recorded_at='2025-09-26T10:02:00Z')
    assert p['recorded_at'] == '2025-09-26T10:02:00Z'


def test_forecast_uses_both_fresh_quotes_and_keeps_reference_feature_block():
    a, o = artifact()
    o['observed_offers'][0]['observed_at'] = '2025-09-26T09:57:00Z'
    with pytest.raises(ValueError, match='fresh'):
        predict(a, o)
    a, o = artifact()
    a['models']['reference'] = deepcopy(a['models']['revision'])
    with pytest.raises(ValueError, match='feature blocks'):
        predict(a, o)
    a, o = artifact()
    a['training'].append({'observation_id':o['observation_id']})
    with pytest.raises(ValueError, match='own fit'):
        predict(a, o)


def test_paper_lock_uses_exact_price_and_ties_without_later_improvement():
    a, o = artifact()
    p = predict(a, o)
    original = deepcopy(o)
    entry = study.paper_selection(o, p, locked_at='2025-09-26T10:00:11Z')
    assert entry['side'] == 'under' and entry['sportsbook'] == 'draftkings'
    assert entry['decimal_odds'] == 1.95 and entry['units_risked'] == 1.
    assert entry['quote_age_seconds_at_lock'] == 11 and not entry['acceptance_verified']
    with pytest.raises(ValueError, match='stale'):
        study.paper_selection(o, p, locked_at='2025-09-26T10:02:01Z')
    o['observed_offers'][0]['under_decimal_odds'] = 2.1
    with pytest.raises(ValueError, match='differ'):
        study.paper_selection(o, p)
    assert original['observed_offers'][0]['under_decimal_odds'] == entry['decimal_odds']


def test_wrong_line_offer_cannot_receive_probability_at_reference_line():
    a, o = artifact()
    o['observed_offers'][1].update(line=49.5, under_decimal_odds=3.)
    p = predict(a, o)
    assert study.paper_selection(o, p)['sportsbook'] == 'draftkings'


def test_original_probabilities_and_prices_determine_win_loss_and_log_scores():
    a, o = artifact()
    p = predict(a, o)
    paper = study.paper_selection(o, p)
    label = {'target_kind':'final_total','observation_id':o['observation_id'],'game_id':o['game_id'],
             'target_available_at':'2025-09-28T02:00:00Z','label_under':1,'final_total':40}
    won = study.grade_prediction(p, label, paper)
    assert won['profit_units'] == pytest.approx(.95)
    assert won['scores']['revision']['log_loss'] == pytest.approx(-np.log(p['prediction']['revision']))
    label['label_under'] = 0
    lost = study.grade_prediction(p, label, paper)
    assert lost['profit_units'] == -1.
    assert lost['prediction_sha256'] == won['prediction_sha256']
    assert lost['label_sha256'] != won['label_sha256']
    saturated = deepcopy(p)
    saturated['prediction']['revision'] = 1.
    saturated['logits']['revision'] = 1000.
    assert study.grade_prediction(saturated, label)['scores']['revision']['log_loss'] == 1000.


def test_optimizer_failure_disables_entire_target_pair_without_old_artifact(monkeypatch):
    rows, labels, _ = data()
    monkeypatch.setattr(models, 'fit_probability', lambda *a, **kw: (_ for _ in ()).throw(models.ModelFitError()))
    a = study.fit_pair(rows, labels, cutoff=CUTOFF, target_kind='final_total', protocol_sha256=PROTOCOL)
    assert a['status'] == 'fit_failed' and a['models'] is None
