"""Pure reports for the unvalidated, fixed prospective revision experiment.

No files, source requests, fitting or betting actions occur here. Each forecast
record contains ``prediction``, ``label`` and ``grade``; unavailable labels and
grades are both None. Each paper record additionally contains ``paper``. The
caller retains append-only histories and supplies the latest valid label whose
actual receipt is at or before report_cutoff. Future grades are counted missing,
never backdated; their existence does not recover a discarded earlier version.

Record references and supplied scores are checked independently. This cannot
certify underlying HTTP bytes, artifact publication or sportsbook acceptance:
the source adapter and immutable archive own those separate trust boundaries.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

import numpy as np

VERSION = 'weather-revision-evaluation-v1-20260909'
STUDY_VERSION = 'weather-revision-study-v1-20260909'
ZONE = ZoneInfo('America/New_York')
END = datetime(2027, 2, 1, tzinfo=ZONE).astimezone(timezone.utc)
REPORT_AT = datetime(2027, 2, 8, 12, tzinfo=timezone.utc)
BOOTSTRAP_DRAWS = 10000
BOOTSTRAP_SEED = 20260909
RELIABILITY_EDGES = tuple(i / 10 for i in range(11))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _utc(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid record timestamp') from error
    _require(value.tzinfo is not None and value.utcoffset() is not None, 'Timezone-aware record required')
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), 'Finite numeric value required')
    return float(value)


def _same(actual, expected, message='Stored score differs from independent recomputation'):
    _require(math.isclose(_number(actual), expected, rel_tol=1e-10, abs_tol=1e-12), message)


def _week(kickoff):
    date = _utc(kickoff).astimezone(ZONE).date()
    return (date - timedelta(days=date.weekday())).isoformat()


def _game(value):
    _require(isinstance(value, (str, int)) and not isinstance(value, bool)
             and str(value).isdigit() and int(value) > 0, 'Positive canonical game ID required')
    return str(int(value))


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SHA256 reference required')
    return value


def _probability_from_logit(value):
    value = _number(value)
    return 1 / (1 + math.exp(-value)) if value >= 0 else math.exp(value) / (1 + math.exp(value))


def _log_loss(logit, outcome):
    # Avoid subtracting two nearly equal large numbers for a correct forecast.
    return float(np.logaddexp(0., -logit if outcome else logit))


def _prediction(record):
    p = record['prediction']
    _require(p.get('schema_version') == 'weather-revision-prediction-v1'
             and p.get('study_version') == STUDY_VERSION, 'Prediction is outside the fixed revision study')
    kind = p.get('target_kind')
    _require(kind in ('final_total', 'market_movement'), 'Unknown target kind')
    _require(isinstance(p.get('observation_id'), str) and p['observation_id'], 'Observation identity required')
    _game(p['game_id'])
    _hash(p['observation_sha256'])
    _hash(p['artifact_sha256'])
    _hash(p['protocol_sha256'])
    available, decision, recorded, kickoff = [_utc(p[k]) for k in ('artifact_available_at', 'input_cutoff', 'recorded_at', 'kickoff')]
    _require(available <= decision <= recorded < kickoff < END, 'Prediction chronology or fixed game endpoint is invalid')
    _require(_utc(p['inference_completed_at']) == recorded, 'Prediction inference time differs from its immutable record')
    _require((recorded - decision).total_seconds() <= 120, 'Prediction was not fresh at recording')
    expected = {'reference', 'revision', 'price_reference'} if kind == 'final_total' else {'reference', 'revision', 'zero_movement'}
    _require(set(p['prediction']) == expected, 'Unexpected model configuration in prediction')
    if kind == 'final_total':
        _require(set(p['logits']) == expected, 'Probability logits are incomplete')
        for name in sorted(expected):
            value = _number(p['prediction'][name])
            _require(0 <= value <= 1, 'Probability is outside [0,1]')
            _same(value, _probability_from_logit(p['logits'][name]), 'Probability and locked logit differ')
        _require(0 < p['prediction']['price_reference'] < 1, 'Price-reference probability requires valid paired prices')
        if 'reference_line' in p:
            line = _number(p['reference_line'])
            _require(line > 0 and line % 1 == .5, 'Probability requires a half-point reference line')
    else:
        for value in p['prediction'].values():
            _number(value)
        _require(p['prediction']['zero_movement'] == 0, 'Zero-movement benchmark was changed')
    if 'prediction_sha256' in record:
        _require(record['prediction_sha256'] == _digest(p), 'Prediction wrapper hash mismatch')
    return p


def _graded(record, cutoff):
    p = _prediction(record)
    label, grade = record.get('label'), record.get('grade')
    _require((label is None) == (grade is None), 'Label and grade must be supplied together')
    result = {'prediction': p, 'prediction_sha256': _digest(p), 'week': _week(p['kickoff']),
              'scores': None, 'label': None, 'grade': None, 'missing_reason': 'label_unavailable'}
    if label is None:
        return result
    _require(grade.get('prediction_sha256') == result['prediction_sha256'], 'Grade prediction hash mismatch')
    _require(grade.get('label_sha256') == _digest(label), 'Grade label hash mismatch')
    if 'grade_sha256' in record:
        _require(record['grade_sha256'] == _digest(grade), 'Grade wrapper hash mismatch')
    _require(label.get('schema_version') == 'weather-revision-target-v1'
             and label.get('target_kind') == p['target_kind']
             and label.get('observation_id') == p['observation_id']
             and _game(label['game_id']) == _game(p['game_id']), 'Label identity or target mismatch')
    received = _utc(label['target_available_at'])
    _require(_utc(grade['target_available_at']) == received, 'Grade target receipt differs from label')
    _require(received > _utc(p['recorded_at']), 'Target predates its prospective prediction')
    if received > cutoff:
        result['missing_reason'] = 'label_received_after_report_cutoff'
        return result
    scores = {}
    if p['target_kind'] == 'final_total':
        _require(received >= _utc(label.get('reported_kickoff') or p['kickoff']), 'Final target predates kickoff')
        y = label['label_under']
        _require(type(y) is int and y in (0, 1), 'Binary integer target required')
        total = _number(label['final_total'])
        _require(total >= 0 and total.is_integer(), 'Integer final total required')
        if 'reference_line' in p:
            _require(y == int(total < p['reference_line']), 'Binary label disagrees with original total contract')
        for name, value in p['prediction'].items():
            scores[name] = {'log_loss': _log_loss(p['logits'][name], y), 'brier': (value - y) ** 2}
    else:
        _require(received < _utc(p['kickoff']) and 4 <= (received - _utc(p['input_cutoff'])).total_seconds() / 3600 <= 8,
                 'Movement receipt violates the fixed pregame horizon')
        target = _number(label['movement_points'])
        for name, value in p['prediction'].items():
            scores[name] = {'squared_error': (value - target) ** 2}
    _require(set(grade['scores']) == set(scores), 'Unexpected scored model configuration')
    for name, metrics in scores.items():
        _require(set(grade['scores'][name]) == set(metrics), 'Unexpected scored metric')
        for metric, value in metrics.items():
            _same(grade['scores'][name][metric], value)
    result.update(scores=scores, label=label, grade=grade, missing_reason=None)
    return result


def _bootstrap(rows, coverages):
    """Rows are (Eastern week, numerator, denominator); weights stay per game."""
    weekly = {}
    for week, numerator, denominator in rows:
        _require(math.isfinite(numerator) and math.isfinite(denominator) and denominator > 0, 'Invalid bootstrap contribution')
        value = weekly.setdefault(week, [0., 0.])
        value[0] += numerator
        value[1] += denominator
    ordered = sorted(weekly)
    numerator = np.asarray([weekly[w][0] for w in ordered])
    denominator = np.asarray([weekly[w][1] for w in ordered])
    estimate = float(numerator.sum() / denominator.sum()) if ordered else None
    intervals = {str(coverage): None for coverage in coverages}
    if len(ordered) >= 2:
        rng = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
        indices = rng.integers(0, len(ordered), size=(BOOTSTRAP_DRAWS, len(ordered)))
        draws = numerator[indices].sum(axis=1) / denominator[indices].sum(axis=1)
        for coverage in coverages:
            quantiles = {.975: (.0125, .9875), .95: (.025, .975), .99: (.005, .995)}[coverage]
            bounds = np.quantile(draws, quantiles, method='linear')
            intervals[str(coverage)] = {'lower': float(bounds[0]), 'upper': float(bounds[1])}
    return {'estimate': estimate, 'records': len(rows), 'contributing_weeks': len(ordered),
            'intervals': intervals, 'interval_status': 'approximate' if len(ordered) >= 2 else 'fewer_than_two_weeks',
            'weekly': [{'week': w, 'numerator': weekly[w][0], 'denominator': weekly[w][1]} for w in ordered]}


def _reliability(scored, name):
    bins = [{'lower': RELIABILITY_EDGES[i], 'upper': RELIABILITY_EDGES[i + 1], 'count': 0,
             'mean_probability': None, 'observed_under_rate': None} for i in range(10)]
    sums = np.zeros((10, 2))
    for row in scored:
        p = row['prediction']['prediction'][name]
        # searchsorted gives exact internal boundaries to their upper bin.
        index = min(9, int(np.searchsorted(RELIABILITY_EDGES, p, side='right') - 1))
        bins[index]['count'] += 1
        sums[index] += [p, row['label']['label_under']]
    for index, value in enumerate(bins):
        if value['count']:
            value['mean_probability'], value['observed_under_rate'] = (sums[index] / value['count']).tolist()
    return bins


def _forecast_summary(rows, kind):
    selected = [r for r in rows if r['prediction']['target_kind'] == kind]
    scored = [r for r in selected if r['scores'] is not None]
    metric = 'log_loss' if kind == 'final_total' else 'squared_error'
    names = ('reference', 'revision', 'price_reference') if kind == 'final_total' else ('reference', 'revision', 'zero_movement')
    means = {name: {m: float(np.mean([r['scores'][name][m] for r in scored])) if scored else None
                    for m in (('log_loss', 'brier') if kind == 'final_total' else ('squared_error',))} for name in names}
    comparison = _bootstrap([(r['week'], r['scores']['revision'][metric] - r['scores']['reference'][metric], 1.) for r in scored], (.975,))
    output = {'locked_forecasts': len(selected), 'scored_forecasts': len(scored),
              'unresolved_forecasts': len(selected) - len(scored),
              'missing_reasons': dict(Counter(r['missing_reason'] for r in selected if r['missing_reason'])),
              'mean_scores': means, 'paired_revision_minus_reference': comparison,
              'context_changed_labels': sum(r['label'].get('context_changed') is True for r in scored)}
    if kind == 'final_total':
        output['reliability'] = {name: _reliability(scored, name) for name in names}
    return output


def _paper(record, row, cutoff):
    p, paper = row['prediction'], record['paper']
    _require(p['target_kind'] == 'final_total' and paper.get('schema_version') == 'weather-revision-paper-entry-v1'
             and paper.get('study_version') == STUDY_VERSION, 'Paper record is outside the probability study')
    _require(paper['prediction_sha256'] == row['prediction_sha256'], 'Paper prediction hash mismatch')
    _require(paper['observation_id'] == p['observation_id'] and _game(paper['game_id']) == _game(p['game_id'])
             and _utc(paper['kickoff']) == _utc(p['kickoff']), 'Paper game or observation mismatch')
    locked, quoted = _utc(paper['recorded_at']), _utc(paper['quote_observed_at'])
    _require(_utc(p['recorded_at']) <= locked < _utc(p['kickoff']) and locked <= cutoff
             and 0 <= (locked - quoted).total_seconds() <= 120, 'Paper lock chronology or freshness invalid')
    _require(_utc(paper['inference_completed_at']) == _utc(p['inference_completed_at'])
             and _utc(paper['offer_checked_at']) == locked
             and paper['offer_check_kind'] == 'current_capture_receipt', 'Paper offer-check record differs from inference or lock')
    _same(paper['quote_age_seconds_at_lock'], (locked - quoted).total_seconds(), 'Paper quote-age record differs')
    _require(paper['side'] in ('under', 'over') and paper['sportsbook'] in ('draftkings', 'fanduel'), 'Unprescribed paper side or book')
    line, odds = _number(paper['line']), _number(paper['decimal_odds'])
    _require(line > 0 and line % 1 == .5 and odds > 1 and _number(paper['units_risked']) == 1, 'Invalid half-point paper contract or unit risk')
    if 'reference_line' in p:
        _require(line == p['reference_line'], 'Paper line differs from prediction contract')
    _require(paper.get('acceptance_verified') is False and paper.get('actual_bet_placed') is False, 'Paper report cannot claim accepted cash bets')
    probability = p['prediction']['revision'] if paper['side'] == 'under' else 1 - p['prediction']['revision']
    _same(paper['modeled_win_probability'], probability, 'Paper probability differs from locked prediction')
    expected = odds * probability - 1
    _same(paper['modeled_ev'], expected, 'Paper EV differs from exact offered contract')
    _require(expected > 0, 'Paper entry violates strictly positive model-EV rule')
    if 'paper_sha256' in record:
        _require(record['paper_sha256'] == _digest(paper), 'Paper wrapper hash mismatch')
    profit, outcome = None, 'unresolved'
    if row['scores'] is not None:
        grade = row['grade']
        if 'paper_sha256' in grade:
            _require(grade['paper_sha256'] == _digest(paper), 'Grade paper hash mismatch')
        _require(_utc(row['label']['target_available_at']) > locked, 'Paper locked after its label was available')
        y = row['label']['label_under']
        _require(y == int(row['label']['final_total'] < line), 'Paper total and binary label disagree')
        win = bool(y) if paper['side'] == 'under' else not bool(y)
        outcome, profit = ('win', odds - 1) if win else ('loss', -1.)
        _require(grade.get('paper_result') == outcome, 'Stored paper settlement differs')
        _same(grade['profit_units'], profit, 'Stored paper profit differs from locked price')
    return {'week': row['week'], 'game_id': _game(p['game_id']), 'profit': profit, 'outcome': outcome,
            'sportsbook': paper['sportsbook'], 'side': paper['side'], 'quote_age_seconds': (locked - quoted).total_seconds()}


def _paper_summary(rows):
    settled = [r for r in rows if r['profit'] is not None]
    unresolved = len(rows) - len(settled)
    known_profit = sum(r['profit'] for r in settled)
    complete = _bootstrap([(r['week'], r['profit'], 1.) for r in rows], (.95, .99)) if not unresolved else None
    conservative = _bootstrap([(r['week'], r['profit'] if r['profit'] is not None else -1., 1.) for r in rows], (.95, .99))
    weeks = sorted({r['week'] for r in rows})
    leave_one_out = []
    for week in weeks:
        remaining = [r for r in rows if r['week'] != week]
        all_settled = all(r['profit'] is not None for r in remaining)
        leave_one_out.append({'omitted_week': week, 'locked_positions': len(remaining),
            'full_cohort_roi': sum(r['profit'] for r in remaining) / len(remaining) if remaining and all_settled else None,
            'unresolved_as_loss_roi': sum(r['profit'] if r['profit'] is not None else -1. for r in remaining) / len(remaining) if remaining else None})
    ages = [r['quote_age_seconds'] for r in rows]
    return {'locked_positions': len(rows), 'total_units_risked': len(rows), 'settled_positions': len(settled),
            'unresolved_positions': unresolved, 'wins': sum(r['outcome'] == 'win' for r in rows),
            'losses': sum(r['outcome'] == 'loss' for r in rows),
            'full_cohort_status': 'no_positions' if not rows else 'incomplete' if unresolved else 'complete',
            'full_cohort_roi': complete['estimate'] if complete else None,
            'full_cohort_uncertainty': complete,
            'settled_only_descriptive': {'positions': len(settled), 'profit_units': known_profit,
                'roi': known_profit / len(settled) if settled else None, 'is_partial_cohort': bool(unresolved)},
            'unresolved_as_full_loss': conservative, 'active_locked_position_weeks': len(weeks),
            'by_sportsbook': dict(Counter(r['sportsbook'] for r in rows)),
            'by_side': dict(Counter(r['side'] for r in rows)), 'leave_one_week_out': leave_one_out,
            'quote_age_seconds': {'minimum': min(ages), 'median': float(np.median(ages)), 'maximum': max(ages)} if ages else None}


def evaluate(forecast_records, paper_records=(), *, report_cutoff):
    """Evaluate supplied immutable records; the fixed final deadline caps time.

    ``forecast_records`` includes every original forecast, even when ungraded.
    ``paper_records`` adds paper bodies to the matching prediction/label/grade
    wrapper; its selected label must match that forecast's report-cutoff label.
    Duplicate game/target forecasts or paper game entries fail rather than
    inflate evidence. Later-created records are excluded from an earlier report.
    """
    requested = _utc(report_cutoff)
    cutoff = min(requested, REPORT_AT)
    rows, forecasts, seen, protocol_hashes = [], {}, set(), set()
    future_predictions = future_papers = 0
    for record in forecast_records:
        prediction = _prediction(record)
        if _utc(prediction['recorded_at']) > cutoff:
            future_predictions += 1
            continue
        key = (_game(prediction['game_id']), prediction['target_kind'])
        _require(key not in seen, 'Duplicate game/target forecast')
        seen.add(key)
        row = _graded(record, cutoff)
        forecasts[row['prediction_sha256']] = row
        protocol_hashes.add(prediction['protocol_sha256'])
        rows.append(row)
    _require(len(protocol_hashes) <= 1, 'Cannot pool changed protocol versions')
    paper_rows, paper_games = [], set()
    for record in paper_records:
        if _utc(record['paper']['recorded_at']) > cutoff:
            future_papers += 1
            continue
        row = _graded(record, cutoff)
        _require(row['prediction_sha256'] in forecasts, 'Paper entry lacks its complete forecast record')
        original = forecasts[row['prediction_sha256']]
        _require(row['missing_reason'] == original['missing_reason'] and
                 (row['label'] is None or _digest(row['label']) == _digest(original['label'])), 'Paper and forecast use different report-cutoff targets')
        gid = _game(row['prediction']['game_id'])
        _require(gid not in paper_games, 'Duplicate paper game entry')
        paper_games.add(gid)
        paper_rows.append(_paper(record, row, cutoff))
    rows.sort(key=lambda row: (row['week'], int(_game(row['prediction']['game_id'])), row['prediction']['target_kind']))
    paper_rows.sort(key=lambda row: (row['week'], int(row['game_id'])))
    weeks = sorted({r['week'] for r in rows})
    last = _week(min(cutoff, END - timedelta(microseconds=1)))
    calendar_weeks = max(0, (datetime.fromisoformat(last).date() - datetime.fromisoformat(weeks[0]).date()).days // 7 + 1) if weeks else 0
    active_elapsed = sum(week <= last for week in weeks)
    result = {'schema_version': VERSION, 'study_version': STUDY_VERSION,
              'requested_report_cutoff': _iso(requested), 'effective_report_cutoff': _iso(cutoff),
              'fixed_final_report_cutoff': _iso(REPORT_AT), 'protocol_sha256': next(iter(protocol_hashes), None),
              'forecast_record_count': len(rows), 'excluded_later_prediction_records': future_predictions,
              'excluded_later_paper_records': future_papers,
              'calendar_weeks_since_first_forecast_kickoff': calendar_weeks,
              'zero_forecast_weeks_through_report': max(0, calendar_weeks - active_elapsed),
              'probability': _forecast_summary(rows, 'final_total'), 'movement': _forecast_summary(rows, 'market_movement'),
              'paper': _paper_summary(paper_rows),
              'bootstrap': {'draws': BOOTSTRAP_DRAWS, 'rng': 'PCG64', 'seed': BOOTSTRAP_SEED,
                  'reset_per_metric': True, 'quantile_method': 'linear', 'paired_model_coverage': .975,
                  'roi_descriptive_coverages': [.95, .99]},
              'record_hash_references_checked': True, 'real_data_fit_performed': False,
              'automatic_promotion': False, 'high_confidence_edge_claim': False,
              'limitations': [
                  'Observed-price paper results are not accepted wagers or cash profitability.',
                  'Weekly percentile intervals are approximate; recurring teams and expanding training induce additional dependence.',
                  '97.5% model-score intervals cover a local two-comparison sensitivity, not profitability, diagnostics or all prior research searches.',
                  'Unresolved labels remain missing; no cancellation or administrative zero-profit settlement is invented.',
                  'Caller must retain target history and supply its latest valid cutoff-specific version; hashes do not certify original source contents.']}
    # Ensure nonfinite calculations cannot escape in a purported JSON report.
    json.dumps(result, allow_nan=False)
    return result
