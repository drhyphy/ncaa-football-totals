"""Prospective revision-study math and immutable record contracts.

No data fetching or wagering. The orchestrator supplies independently validated
observations, original outcome receipts, and an actually published weekly model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

import numpy as np

from . import weather_revision_models as models

VERSION = 'weather-revision-study-v1-20260909'
FRESH_SECONDS = 120
ZONE = ZoneInfo('America/New_York')
END = datetime(2027, 2, 1, tzinfo=ZONE).astimezone(timezone.utc)
REPORT_AT = datetime(2027, 2, 8, 12, tzinfo=timezone.utc)


def utc(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('An original timezone-aware timestamp is required')
    return value.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat().replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def weekly_cutoff(at):
    local = utc(at).astimezone(ZONE)
    day = local.date() - timedelta(days=local.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=ZONE).astimezone(timezone.utc)


def feature_columns(observations):
    return {name: np.asarray([o['features'][name] for o in observations], dtype=float)
            for name in models.ALL_FEATURES}


def training_sample(observations, labels, *, cutoff, target_kind):
    """Select only earlier completed football weeks and genuinely earlier labels.

    ``labels`` maps observation IDs to the latest validated label available at
    the cutoff. The caller must retain all label revisions when building it.
    """
    cutoff = utc(cutoff)
    if weekly_cutoff(cutoff) != cutoff or target_kind not in ('final_total', 'market_movement'):
        raise ValueError('Fixed Monday cutoff and known target required')
    chosen, seen, weeks = [], set(), set()
    for o in sorted(observations, key=lambda o: (utc(o['kickoff']), str(o['game_id']))):
        label = labels.get(o['observation_id'])
        if not label or label.get('target_kind') != target_kind:
            continue
        if label.get('observation_id') != o['observation_id'] or str(label.get('game_id')) != str(o['game_id']):
            raise ValueError('Label belongs to a different observation or game')
        kickoff, decision = utc(o['kickoff']), utc(o['decision_at'])
        available = utc(label['target_available_at'])
        week = weekly_cutoff(kickoff)
        if week >= cutoff or available >= cutoff:
            continue
        if not decision < available or decision >= kickoff:
            raise ValueError('Training label chronology is invalid')
        if target_kind == 'final_total':
            if available < utc(label.get('reported_kickoff') or o['kickoff']):
                raise ValueError('Final total predates kickoff')
            if o['reference_line'] % 1 != .5:
                continue
            if label['label_under'] not in (0, 1):
                raise ValueError('Invalid binary outcome')
        elif available >= kickoff:
            raise ValueError('Market movement target is not pregame')
        gid = str(o['game_id'])
        if gid in seen:
            raise ValueError('Repeated designated game cannot inflate training')
        seen.add(gid)
        weeks.add(week)
        chosen.append((o, label))
    return chosen, {'games': len(chosen), 'completed_weeks': len(weeks),
                    'ready': len(chosen) >= models.MIN_TRAIN_GAMES and len(weeks) >= models.MIN_TRAIN_WEEKS,
                    'cutoff': iso(cutoff), 'target_kind': target_kind}


def serialize_model(model):
    out = {'feature_names': list(model.standardizer.names),
           'mean': model.standardizer.mean.tolist(), 'scale': model.standardizer.scale.tolist(),
           'coefficients': model.coefficients.tolist(), 'training_rows': model.training_rows}
    if isinstance(model, models.ProbabilityModel):
        out.update(kind='probability', optimizer_iterations=model.optimizer_iterations)
    elif isinstance(model, models.MovementModel):
        out.update(kind='movement', target_mean=model.target_mean, target_scale=model.target_scale)
    else:
        raise ValueError('Unknown model type')
    return out


def deserialize_model(value):
    names = tuple(value['feature_names'])
    if names not in (models.BASE_FEATURES, models.ALL_FEATURES):
        raise ValueError('Artifact has an unprescribed feature block')
    mean, scale, coefficients = [np.asarray(value[k], dtype=float) for k in ('mean', 'scale', 'coefficients')]
    if (mean.shape != (len(names),) or scale.shape != mean.shape or coefficients.shape != (len(names) + 1,)
            or not all(np.isfinite(v).all() for v in (mean, scale, coefficients)) or np.any(scale <= 0)):
        raise ValueError('Invalid stored model parameters')
    transform = models.Standardizer(names, mean, scale)
    if value['kind'] == 'probability':
        return models.ProbabilityModel(transform, coefficients, value['training_rows'], value['optimizer_iterations'])
    if value['kind'] == 'movement' and np.isfinite(value['target_mean']) and np.isfinite(value['target_scale']) and value['target_scale'] >= 0:
        return models.MovementModel(transform, coefficients, value['target_mean'], value['target_scale'], value['training_rows'])
    raise ValueError('Invalid stored target model')


def fit_pair(observations, labels, *, cutoff, target_kind, protocol_sha256):
    """Fit the fixed reference and challenger together or return unavailable.

    This result has no availability timestamp: the caller must persist all model
    bytes first and record their actual publication receipt afterward.
    """
    sample, readiness = training_sample(observations, labels, cutoff=cutoff, target_kind=target_kind)
    result = {'schema_version': 'weather-revision-fit-v1', 'study_version': VERSION,
              'model_version': models.VERSION, 'protocol_sha256': protocol_sha256,
              **readiness, 'status': 'insufficient_training', 'models': None,
              'training': [{'observation_id': o['observation_id'], 'observation_sha256': digest(o),
                            'label_sha256': digest(label), 'label_available_at': label['target_available_at']}
                           for o, label in sample]}
    if not readiness['ready']:
        return result
    rows = [o for o, _ in sample]
    x = feature_columns(rows)
    try:
        if target_kind == 'final_total':
            q = [o['q_under'] for o in rows]
            y = [label['label_under'] for _, label in sample]
            fit = lambda revisions: models.fit_probability(x, q, y, with_revisions=revisions)
        else:
            y = [label['movement_points'] for _, label in sample]
            fit = lambda revisions: models.fit_movement(x, y, with_revisions=revisions)
        result['models'] = {name: serialize_model(fit(revisions))
                            for name, revisions in [('reference', False), ('revision', True)]}
        result['status'] = 'fitted'
    except (models.ModelFitError, ValueError, FloatingPointError):
        result['models'] = None
        result['status'] = 'fit_failed'
    return result


def make_prediction(observation, artifact, *, available_at, recorded_at, protocol_sha256):
    """One genuinely prospective prediction, using a fresh current observation.

    Available-at must be a post-persistence receipt, never a guessed fit time.
    The returned values alone do not establish an executable or profitable edge.
    """
    clock = recorded_at if callable(recorded_at) else lambda: recorded_at
    now, available = utc(clock()), utc(available_at)
    decision, kickoff = utc(observation['decision_at']), utc(observation['kickoff'])
    offers = observation['observed_offers']
    quote_times = [utc(q['observed_at']) for q in offers]
    if (not quote_times or decision != max(quote_times) or not available <= min(quote_times)
            or not decision <= now < kickoff < END
            or any(not 0 <= (now - t).total_seconds() <= FRESH_SECONDS for t in quote_times)):
        raise ValueError('Prediction is not prospective, pregame and fresh')
    if (artifact.get('status') != 'fitted' or artifact.get('study_version') != VERSION
            or artifact.get('model_version') != models.VERSION
            or artifact.get('target_kind') not in ('final_total', 'market_movement')
            or artifact.get('protocol_sha256') != protocol_sha256
            or utc(artifact['cutoff']) != weekly_cutoff(now)):
        raise ValueError('Current-week frozen study artifact unavailable')
    if observation['observation_id'] in {r['observation_id'] for r in artifact['training']}:
        raise ValueError('An observation cannot appear in its own fit')
    x = feature_columns([observation])
    fitted = {name: deserialize_model(artifact['models'][name]) for name in ('reference', 'revision')}
    if (fitted['reference'].standardizer.names != models.BASE_FEATURES
            or fitted['revision'].standardizer.names != models.ALL_FEATURES):
        raise ValueError('Reference and revision feature blocks were changed')
    expected = 'probability' if artifact['target_kind'] == 'final_total' else 'movement'
    if any(artifact['models'][name]['kind'] != expected for name in fitted):
        raise ValueError('Artifact target kind does not match its models')
    if artifact['target_kind'] == 'final_total':
        logits = {name: float(model.predict_logits(x, [observation['q_under']])[0]) for name, model in fitted.items()}
        prediction = {name: float(model.predict_under(x, [observation['q_under']])[0]) for name, model in fitted.items()}
        prediction['price_reference'] = float(observation['q_under'])
        logits['price_reference'] = float(np.log(observation['q_under']) - np.log1p(-observation['q_under']))
    else:
        logits = None
        prediction = {name: float(model.predict(x)[0]) for name, model in fitted.items()}
        prediction['zero_movement'] = 0.
    finished = utc(clock())
    if (finished < now or finished >= kickoff or weekly_cutoff(finished) != utc(artifact['cutoff'])
            or any(not 0 <= (finished - t).total_seconds() <= FRESH_SECONDS for t in quote_times)):
        raise ValueError('Price freshness expired during inference')
    return {'schema_version': 'weather-revision-prediction-v1', 'study_version': VERSION,
            'observation_id': observation['observation_id'], 'observation_sha256': digest(observation),
            'game_id': observation['game_id'], 'kickoff': observation['kickoff'],
            'reference_line': observation['reference_line'],
            'target_kind': artifact['target_kind'], 'input_cutoff': observation['decision_at'],
            'recorded_at': iso(finished), 'inference_completed_at': iso(finished),
            'artifact_sha256': digest(artifact), 'artifact_available_at': iso(available),
            'protocol_sha256': protocol_sha256, 'prediction': prediction, 'logits': logits}


def paper_selection(observation, prediction, *, locked_at=None):
    """Largest strictly positive EV, exact contract, fixed side then book ties."""
    if prediction['target_kind'] != 'final_total':
        return None
    if prediction['observation_sha256'] != digest(observation):
        raise ValueError('Prediction and observed offer inputs differ')
    p = prediction['prediction']['revision']
    possibilities = []
    for offer in observation['observed_offers']:
        if offer['line'] != observation['reference_line']:
            continue
        if offer['sportsbook'] not in ('draftkings', 'fanduel'):
            raise ValueError('Unexpected sportsbook')
        ev = models.same_line_ev(p, reference_line=observation['reference_line'], offered_line=offer['line'],
                                over_decimal=offer['over_decimal_odds'], under_decimal=offer['under_decimal_odds'])
        for side in ('under', 'over'):
            if ev[side + '_ev'] > 0:
                possibilities.append((ev[side + '_ev'], side, offer))
    if not possibilities:
        return None
    expected, side, offer = min(possibilities, key=lambda item: (-item[0], ('under', 'over').index(item[1]),
                                              ('draftkings', 'fanduel').index(item[2]['sportsbook'])))
    locked = utc(locked_at() if callable(locked_at) else locked_at or prediction['recorded_at'])
    age = (locked - utc(offer['observed_at'])).total_seconds()
    if not utc(prediction['recorded_at']) <= locked < utc(observation['kickoff']) or not 0 <= age <= FRESH_SECONDS:
        raise ValueError('Selected offer is stale or lock predates inference')
    return {'schema_version': 'weather-revision-paper-entry-v1', 'study_version': VERSION,
            'policy_id': 'weather-revision-probability-v1-20260909',
            'protocol_sha256': prediction['protocol_sha256'],
            'observation_id': observation['observation_id'], 'game_id': observation['game_id'],
            'kickoff': observation['kickoff'], 'recorded_at': iso(locked),
            'inference_completed_at': prediction['inference_completed_at'],
            'context_id': observation['context_id'], 'offer_check_kind': 'current_capture_receipt',
            'offer_checked_at': iso(locked), 'quote_age_seconds_at_lock': age,
            'prediction_sha256': digest(prediction), 'sportsbook': offer['sportsbook'],
            'side': side, 'line': offer['line'], 'decimal_odds': offer[side + '_decimal_odds'],
            'quote_id': offer['quote_id'], 'quote_observed_at': offer['observed_at'],
            'quote_receipt_path': offer['receipt_path'], 'modeled_ev': expected,
            'modeled_win_probability': p if side == 'under' else 1 - p,
            'units_risked': 1., 'acceptance_verified': False, 'actual_bet_placed': False}


def grade_prediction(prediction, label, paper=None):
    """Descriptive grading of the original decision; no rewritten predictions."""
    if (label['target_kind'] != prediction['target_kind'] or label.get('observation_id') != prediction['observation_id']
            or str(label.get('game_id')) != str(prediction['game_id'])
            or utc(label['target_available_at']) <= utc(prediction['recorded_at'])):
        raise ValueError('Label target or chronology mismatch')
    result = {'prediction_sha256': digest(prediction), 'label_sha256': digest(label),
              'target_available_at': label['target_available_at'], 'scores': {}}
    if prediction['target_kind'] == 'final_total':
        y = label['label_under']
        if y not in (0, 1):
            raise ValueError('Invalid binary label')
        for name, p in prediction['prediction'].items():
            loss = models.binary_log_loss_from_logits([prediction['logits'][name]], [y])
            result['scores'][name] = {'log_loss': loss, 'brier': float((p - y) ** 2)}
        if paper:
            if paper['prediction_sha256'] != digest(prediction):
                raise ValueError('Paper entry refers to another prediction')
            win = bool(y) if paper['side'] == 'under' else not bool(y)
            result.update(paper_sha256=digest(paper), paper_result='win' if win else 'loss',
                          profit_units=paper['decimal_odds'] - 1 if win else -1.)
    else:
        target = label['movement_points']
        result['scores'] = {name: {'squared_error': float((value - target) ** 2)}
                            for name, value in prediction['prediction'].items()}
    return result
