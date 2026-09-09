"""Synthetic prospective records only; no archive, labels, fits or network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np
import pytest

from ncaaf_model import weather_revision_evaluation as evaluation


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def logit(p):
    return -1000. if p == 0 else 1000. if p == 1 else math.log(p / (1 - p))


def softplus(x):
    return max(0., x) + math.log1p(math.exp(-abs(x)))


def forecast(gid=1, week=0, *, kind='final_total', y=1, revision=.7, pending=False, protocol='c' * 64):
    kickoff = datetime(2026, 9, 19, 20, tzinfo=timezone.utc) + timedelta(weeks=week)
    decision = kickoff - timedelta(hours=30)
    recorded = decision + timedelta(seconds=1)
    values = {'reference': .55, 'revision': revision, 'price_reference': .5} if kind == 'final_total' else {
        'reference': .5, 'revision': 1.5, 'zero_movement': 0.}
    p = {'schema_version': 'weather-revision-prediction-v1', 'study_version': evaluation.STUDY_VERSION,
         'game_id': str(gid), 'observation_id': 'observation-' + str(gid), 'observation_sha256': 'a' * 64,
         'artifact_sha256': 'b' * 64, 'protocol_sha256': protocol, 'target_kind': kind,
         'kickoff': iso(kickoff), 'reference_line': 54.5,
         'artifact_available_at': iso(decision - timedelta(hours=1)), 'input_cutoff': iso(decision),
         'recorded_at': iso(recorded), 'inference_completed_at': iso(recorded), 'prediction': values,
         'logits': {name: logit(value) for name, value in values.items()} if kind == 'final_total' else None}
    if pending:
        return {'prediction': p, 'label': None, 'grade': None}
    label = {'schema_version': 'weather-revision-target-v1', 'game_id': p['game_id'],
             'observation_id': p['observation_id'], 'target_kind': kind,
             'target_available_at': iso(kickoff + timedelta(hours=5) if kind == 'final_total' else decision + timedelta(hours=6))}
    if kind == 'final_total':
        label.update(label_under=y, final_total=49 if y else 61)
        scores = {name: {'log_loss': softplus(-p['logits'][name] if y else p['logits'][name]),
                         'brier': (value - y) ** 2} for name, value in values.items()}
    else:
        label['movement_points'] = 2.
        scores = {name: {'squared_error': (value - 2.) ** 2} for name, value in values.items()}
    grade = {'prediction_sha256': digest(p), 'label_sha256': digest(label),
             'target_available_at': label['target_available_at'], 'scores': scores}
    return {'prediction': p, 'label': label, 'grade': grade}


def paper(record, side='under', price=2.1):
    result = deepcopy(record)
    p = result['prediction']
    quoted = datetime.fromisoformat(p['input_cutoff'].replace('Z', '+00:00'))
    locked = quoted + timedelta(seconds=3)
    probability = p['prediction']['revision'] if side == 'under' else 1 - p['prediction']['revision']
    result['paper'] = {'schema_version': 'weather-revision-paper-entry-v1', 'study_version': evaluation.STUDY_VERSION,
        'game_id': p['game_id'], 'observation_id': p['observation_id'], 'kickoff': p['kickoff'],
        'prediction_sha256': digest(p), 'recorded_at': iso(locked), 'quote_observed_at': iso(quoted),
        'inference_completed_at': p['inference_completed_at'], 'offer_checked_at': iso(locked),
        'offer_check_kind': 'current_capture_receipt', 'quote_age_seconds_at_lock': 3.,
        'sportsbook': 'draftkings', 'side': side, 'line': 54.5, 'decimal_odds': price, 'units_risked': 1.,
        'acceptance_verified': False, 'actual_bet_placed': False,
        'modeled_win_probability': probability, 'modeled_ev': price * probability - 1}
    if result['grade']:
        win = bool(result['label']['label_under']) if side == 'under' else not bool(result['label']['label_under'])
        result['grade'].update(paper_sha256=digest(result['paper']), paper_result='win' if win else 'loss',
                               profit_units=price - 1 if win else -1.)
    return result


CUTOFF = '2026-12-01T12:00:00Z'


def test_empty_report_has_no_edge_zero_roi_or_interval():
    result = evaluation.evaluate([], report_cutoff=CUTOFF)
    assert result['forecast_record_count'] == 0
    assert result['paper']['full_cohort_roi'] is None
    assert result['paper']['full_cohort_status'] == 'no_positions'
    assert result['paper']['unresolved_as_full_loss']['estimate'] is None
    assert result['probability']['paired_revision_minus_reference']['intervals']['0.975'] is None
    assert result['high_confidence_edge_claim'] is False and result['automatic_promotion'] is False


def test_all_forecasts_scored_including_unselected_games_and_raw_q():
    rows = [forecast(1, 0), forecast(2, 1, y=0), forecast(3, 1, pending=True)]
    result = evaluation.evaluate(rows, [paper(rows[0])], report_cutoff=CUTOFF)
    probability = result['probability']
    assert probability['locked_forecasts'] == 3 and probability['scored_forecasts'] == 2
    assert probability['unresolved_forecasts'] == 1
    assert probability['mean_scores']['price_reference']['log_loss'] == pytest.approx(math.log(2))
    expected = (-math.log(.7) - math.log(.3)) / 2
    assert probability['mean_scores']['revision']['log_loss'] == pytest.approx(expected)
    assert result['paper']['locked_positions'] == 1
    assert probability['paired_revision_minus_reference']['contributing_weeks'] == 2


def test_movement_mse_and_zero_benchmark_are_separate_from_paper_policy():
    rows = [forecast(1, 0, kind='market_movement'), forecast(2, 1, kind='market_movement')]
    result = evaluation.evaluate(rows, report_cutoff=CUTOFF)
    movement = result['movement']
    assert movement['mean_scores'] == {'reference': {'squared_error': 2.25},
                                      'revision': {'squared_error': .25}, 'zero_movement': {'squared_error': 4.}}
    assert movement['paired_revision_minus_reference']['estimate'] == -2.
    assert result['paper']['locked_positions'] == 0


def test_bootstrap_preserves_game_denominators_and_exact_frozen_rng():
    rows = [('2026-09-14', 10., 1.)] + [('2026-09-21', 0., 1.)] * 3
    value = evaluation._bootstrap(rows, (.975,))
    assert value['estimate'] == 2.5  # Mean of week means would incorrectly give 5.
    weeks = [('2026-09-%02d' % (i + 1), float(i * i - 7), float(i + 1)) for i in range(8)]
    result = evaluation._bootstrap(weeks, (.975,))
    rng = np.random.Generator(np.random.PCG64(20260909))
    samples = rng.integers(0, 8, size=(10000, 8))
    independent = [sum(weeks[i][1] for i in chosen) / sum(weeks[i][2] for i in chosen) for chosen in samples]
    low, high = np.quantile(independent, [.0125, .9875], method='linear')
    assert result['intervals']['0.975'] == {'lower': float(low), 'upper': float(high)}
    assert evaluation._bootstrap(list(reversed(weeks)), (.975,)) == result


def test_pending_paper_never_disappears_from_risk_or_receives_zero_profit():
    rows = [forecast(1), forecast(2, 1, pending=True)]
    result = evaluation.evaluate(rows, [paper(r) for r in rows], report_cutoff=CUTOFF)['paper']
    assert result['total_units_risked'] == 2 and result['unresolved_positions'] == 1
    assert result['full_cohort_roi'] is None and result['full_cohort_uncertainty'] is None
    assert result['settled_only_descriptive']['roi'] == pytest.approx(1.1)
    assert result['settled_only_descriptive']['is_partial_cohort'] is True
    assert result['unresolved_as_full_loss']['estimate'] == pytest.approx(.05)
    assert result['unresolved_as_full_loss']['contributing_weeks'] == 2
    assert result['unresolved_as_full_loss']['intervals']['0.95'] == pytest.approx({'lower': -1., 'upper': 1.1})


def test_complete_paper_roi_prices_losses_and_99_interval():
    rows = [forecast(1), forecast(2, 1, y=0)]
    result = evaluation.evaluate(rows, [paper(r) for r in rows], report_cutoff=CUTOFF)['paper']
    assert result['full_cohort_status'] == 'complete'
    assert result['wins'] == 1 and result['losses'] == 1
    assert result['full_cohort_roi'] == pytest.approx(.05)
    assert result['full_cohort_uncertainty'] == result['unresolved_as_full_loss']
    assert result['full_cohort_uncertainty']['intervals']['0.99'] is not None
    assert len(result['leave_one_week_out']) == 2


def test_correct_side_settlement_and_single_week_interval_withheld():
    row = forecast(y=0, revision=.3)
    result = evaluation.evaluate([row], [paper(row, side='over')], report_cutoff=CUTOFF)['paper']
    assert result['full_cohort_roi'] == pytest.approx(1.1)
    assert result['full_cohort_uncertainty']['intervals'] == {'0.95': None, '0.99': None}


def test_future_target_is_unresolved_and_report_deadline_cannot_extend():
    row = forecast()
    row['label']['target_available_at'] = '2027-02-09T12:00:00Z'
    row['grade']['target_available_at'] = row['label']['target_available_at']
    row['grade']['label_sha256'] = digest(row['label'])
    result = evaluation.evaluate([row], [paper(row)], report_cutoff='2027-02-10T12:00:00Z')
    assert result['effective_report_cutoff'] == '2027-02-08T12:00:00Z'
    assert result['probability']['scored_forecasts'] == 0
    assert result['probability']['missing_reasons'] == {'label_received_after_report_cutoff': 1}
    assert result['paper']['unresolved_as_full_loss']['estimate'] == -1.


def test_later_created_predictions_and_papers_not_backfilled_to_earlier_report():
    first, later = forecast(1), forecast(2, 3)
    result = evaluation.evaluate([first, later], [paper(first), paper(later)], report_cutoff='2026-09-25T12:00:00Z')
    assert result['forecast_record_count'] == 1
    assert result['excluded_later_prediction_records'] == result['excluded_later_paper_records'] == 1


def test_extreme_logits_remain_finite_and_reliability_bins_use_fixed_boundaries():
    probabilities = [0., .1, .2, .9, 1.]
    rows = [forecast(i + 1, revision=p, y=1) for i, p in enumerate(probabilities)]
    result = evaluation.evaluate(rows, report_cutoff=CUTOFF)['probability']
    assert math.isfinite(result['mean_scores']['revision']['log_loss'])
    bins = result['reliability']['revision']
    assert [b['count'] for b in bins] == [1, 1, 1, 0, 0, 0, 0, 0, 0, 2]
    assert bins[9]['mean_probability'] == .95 and bins[9]['observed_under_rate'] == 1.


def test_post_lock_context_change_is_retained_and_inputs_are_not_mutated():
    row = forecast(y=0)
    row['label']['context_changed'] = True
    row['label']['context_flags'] = ['venue_changed']
    row['grade']['label_sha256'] = digest(row['label'])
    p = paper(row)
    before = deepcopy((row, p))
    result = evaluation.evaluate([row], [p], report_cutoff=CUTOFF)
    assert result['probability']['context_changed_labels'] == 1
    assert result['paper']['losses'] == 1
    assert (row, p) == before


@pytest.mark.parametrize('mutation,message', [
    ('prediction_hash', 'Grade prediction hash'), ('label_hash', 'Grade label hash'),
    ('score', 'Stored score'), ('probability_logit', 'Probability and locked logit'),
    ('wrong_binary', 'Binary label disagrees'), ('mixed_protocol', 'Cannot pool'),
    ('duplicate', 'Duplicate game/target'), ('target_before_prediction', 'Target predates'),
    ('naive', 'Timezone-aware'), ('paper_hash', 'Grade paper hash'), ('paper_price', 'Paper EV differs'),
    ('paper_duplicate', 'Duplicate paper game'), ('missing_forecast', 'lacks its complete forecast'),
    ('movement_horizon', 'fixed pregame horizon'), ('cash_claim', 'accepted cash bets'),
])
def test_record_tampering_and_cross_study_errors_fail(mutation, message):
    row = forecast(kind='market_movement' if mutation == 'movement_horizon' else 'final_total')
    records, papers = [row], []
    if mutation == 'prediction_hash':
        row['prediction']['artifact_sha256'] = 'e' * 64
    elif mutation == 'label_hash':
        row['label']['final_total'] += 1
    elif mutation == 'score':
        row['grade']['scores']['revision']['log_loss'] += .1
    elif mutation == 'probability_logit':
        row['prediction']['logits']['revision'] += .1
    elif mutation == 'wrong_binary':
        row['label']['final_total'] = 100
        row['grade']['label_sha256'] = digest(row['label'])
    elif mutation == 'mixed_protocol':
        records.append(forecast(2, protocol='e' * 64))
    elif mutation == 'duplicate':
        duplicate = deepcopy(row)
        duplicate['prediction']['game_id'] = '01'
        records.append(duplicate)
    elif mutation == 'target_before_prediction':
        row['label']['target_available_at'] = row['prediction']['input_cutoff']
        row['grade']['target_available_at'] = row['label']['target_available_at']
        row['grade']['label_sha256'] = digest(row['label'])
    elif mutation == 'naive':
        row['prediction']['input_cutoff'] = '2026-09-18T14:00:00'
    elif mutation == 'movement_horizon':
        row['label']['target_available_at'] = '2026-09-18T23:00:00Z'
        row['grade']['target_available_at'] = row['label']['target_available_at']
        row['grade']['label_sha256'] = digest(row['label'])
    else:
        p = paper(row)
        papers = [p]
        if mutation == 'paper_hash':
            p['grade']['paper_sha256'] = 'f' * 64
        elif mutation == 'paper_price':
            p['paper']['decimal_odds'] = 3.
        elif mutation == 'paper_duplicate':
            papers.append(deepcopy(p))
        elif mutation == 'missing_forecast':
            records = []
        elif mutation == 'cash_claim':
            p['paper']['acceptance_verified'] = True
    with pytest.raises(ValueError, match=message):
        evaluation.evaluate(records, papers, report_cutoff=CUTOFF)


def test_paper_and_forecast_cannot_choose_different_label_corrections():
    row = forecast()
    other = paper(forecast(y=0))
    with pytest.raises(ValueError, match='different report-cutoff targets'):
        evaluation.evaluate([row], [other], report_cutoff=CUTOFF)


def test_same_game_can_have_both_targets_but_not_two_probability_forecasts():
    result = evaluation.evaluate([forecast(), forecast(kind='market_movement')], report_cutoff=CUTOFF)
    assert result['probability']['scored_forecasts'] == result['movement']['scored_forecasts'] == 1


def test_eastern_sunday_is_not_next_monday_and_no_interval_from_duplicate_week():
    assert evaluation._week('2026-09-21T02:00:00Z') == '2026-09-14'
    assert evaluation._week('2026-09-21T04:00:00Z') == '2026-09-21'
    result = evaluation._bootstrap([('2026-09-14', 1., 1.), ('2026-09-14', -1., 1.)], (.975,))
    assert result['contributing_weeks'] == 1 and result['intervals']['0.975'] is None


def test_current_core_grade_schema_integrates_without_trusting_its_scores():
    from ncaaf_model.weather_revision_study import grade_prediction

    probability, movement = forecast(), forecast(2, 1, kind='market_movement')
    position = paper(probability)
    position['grade'] = grade_prediction(position['prediction'], position['label'], position['paper'])
    probability['grade'] = position['grade']
    movement['grade'] = grade_prediction(movement['prediction'], movement['label'])
    result = evaluation.evaluate([probability, movement], [position], report_cutoff=CUTOFF)
    assert result['probability']['scored_forecasts'] == result['movement']['scored_forecasts'] == 1
    assert result['paper']['wins'] == 1
    assert position['grade']['paper_sha256'] == digest(position['paper'])
