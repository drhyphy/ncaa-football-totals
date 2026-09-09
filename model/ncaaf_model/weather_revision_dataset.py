"""Pure adapter for the fixed weather-revision observation and label contracts.

No files, network, outcomes, or fitted artifacts are loaded by this module.
The caller supplies source/receipt-verified inventory run records and a verified
``envelope(receipt_path) -> {payload, receipt}`` callback (for example archived
``revision_archive.load_envelope``). Inventory owns complete run enumeration and
first-decision designation before coverage is examined. Its recorded manifest
hashes and source-verification result are a trust boundary, not re-created from
re-serialized JSON here. This adapter independently reconstructs the retained
weather and quote values from those original bodies before making 11 features.

Labels are separate return values; neither helper alters an input observation.
Missing context, weather, quotes, or targets raises ValueError without filling
from another capture. The movement helper selects the immediate scheduled run
before checking any target coverage and never requires that run's weather.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from pathlib import PurePosixPath
import re

from .revision_archive import digest_json
from .teams import normalize_team
from .weather_revision_collector import (
    COLLECTION_PROFILES, ODDS, SUMMARY, VENUES, parse_quote_pairs, summary_identity,
)
from .weather_revision_models import ALL_FEATURES, BASE_FEATURES, REVISION_FEATURES
from .weather_revision_weather import BOUNDS, parse_single_run

VERSION = 'weather-revision-dataset-v1'
FEATURES = ALL_FEATURES


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_timestamp') from exc
    _require(result.tzinfo is not None and result.utcoffset() is not None, 'naive_timestamp')
    return result.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).isoformat().replace('+00:00', 'Z')


def _number(value, reason, bounds=None):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), reason)
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(reason) from exc
    _require(math.isfinite(value), reason)
    if bounds:
        _require(bounds[0] <= value <= bounds[1], reason)
    return float(value)


def _id(value):
    _require(isinstance(value, (str, int)) and not isinstance(value, bool), 'invalid_game_or_team_id')
    _require(str(value).isdigit() and int(value) > 0 and str(int(value)) == str(value), 'noncanonical_game_or_team_id')
    return str(value)


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'invalid_sha256')
    return value


def _path(value):
    _require(isinstance(value, str) and value and not PurePosixPath(value).is_absolute()
             and '..' not in PurePosixPath(value).parts and '\\' not in value, 'unsafe_provenance_path')
    return value


def _receipt(value, path=None):
    _require(isinstance(value, dict) and value.get('schema_version') == 'raw-http-receipt-v1', 'receipt_schema')
    _require(value.get('status_code') == 200 and value.get('transport_error') is None
             and not value.get('body_withheld', False), 'receipt_failed')
    _path(value['receipt_path'])
    _path(value['body_path'])
    _hash(value['body_sha256'])
    if path is not None:
        _require(value['receipt_path'] == path, 'receipt_path_mismatch')
    _require(_time(value['requested_at']) <= _time(value['received_at']), 'receipt_clock_order')
    return value


def _envelope(callback, path, *, url=None, params=None):
    result = callback(_path(path))
    receipt = _receipt(result['receipt'], path)
    if url is not None:
        _require(receipt['request']['url'] == url, 'receipt_source_mismatch')
    if params is not None:
        _require(receipt['request']['params'] == params, 'receipt_request_mismatch')
    return result


def _run_identity(run):
    manifest = run['manifest']
    return str(manifest['run_id']), str(manifest['run_attempt'])


def _scheduled(run):
    manifest = run['manifest']
    profile = manifest.get('collection_profile', 'pilot')
    _require(profile in COLLECTION_PROFILES, 'unknown_collection_profile')
    window = COLLECTION_PROFILES[profile]
    return (manifest.get('trigger') == 'schedule'
            and window['start'] <= _time(manifest['capture_started_at']) < window['end'])


def _run(run):
    _require(run.get('integrity', {}).get('verified') is True, 'source_receipt_integrity_unverified')
    manifest = run['manifest']
    _require(manifest.get('schema_version') == 'weather-revision-capture-v1' and _scheduled(run), 'not_archived_scheduled_capture')
    _require(_time(manifest['capture_started_at']) <= _time(manifest['capture_completed_at']), 'capture_clock_order')
    return {'path': _path(run['path']), 'sha256': _hash(run['sha256']),
            'cohort_sha256': _hash(run['cohort_sha256']),
            'run_id': str(manifest['run_id']), 'run_attempt': str(manifest['run_attempt']),
            'collection_profile': manifest.get('collection_profile', 'pilot'),
            'capture_started_at': manifest['capture_started_at'],
            'capture_completed_at': manifest['capture_completed_at'],
            'git_commit': manifest.get('git_commit'), 'source_files_sha256': deepcopy(manifest.get('provenance', {}))}


def _context(run, gid, envelope):
    row = run['rows'].get(gid)
    _require(isinstance(row, dict), 'game_missing_from_immediate_capture')
    cohort = [r for r in run['cohort']['games'] if str(r.get('game_id')) == gid]
    _require(len(cohort) == 1, 'unique_cohort_game_required')
    _require(all(row.get(k) == cohort[0].get(k) for k in cohort[0]), 'cohort_row_identity_mismatch')
    context = row.get('context')
    _require(isinstance(context, dict) and row.get('context_id') == digest_json(context), 'context_digest_invalid')
    _require(context.get('state') == 'pre' and context.get('indoor') is False
             and context.get('neutral_site') is False, 'unconfirmed_outdoor_pregame_context')
    _require(_id(context['game_id']) == gid and all(context.get(k) == row.get(k) for k in
             ('game_id', 'home_id', 'away_id', 'home_team', 'away_team', 'kickoff')), 'context_identity_mismatch')
    _require(_id(context['home_id']) != _id(context['away_id']), 'duplicate_team_identity')
    original = _envelope(envelope, row['summary_receipt'], url=SUMMARY, params={'event': gid})
    expected = {k: v for k, v in context.items() if k != 'indoor'}
    _require(summary_identity(original['payload'], row) == expected, 'original_official_context_mismatch')
    roof = _envelope(envelope, row['roof_receipt'], url=VENUES + context['venue_id'],
                     params={'lang': 'en', 'region': 'us'})
    _require(isinstance(roof['payload'], dict) and str(roof['payload'].get('id')) == context['venue_id']
             and roof['payload'].get('indoor') is False, 'original_roof_mismatch')
    start, end = _time(run['manifest']['capture_started_at']), _time(run['manifest']['capture_completed_at'])
    for response in (original, roof):
        _require(start <= _time(response['receipt']['requested_at']) <= _time(response['receipt']['received_at']) <= end,
                 'context_receipt_outside_capture')
    return row, max(_time(original['receipt']['received_at']), _time(roof['receipt']['received_at']))


def _weather(run, row, envelope):
    record = row.get('single_run')
    _require(isinstance(record, dict), 'single_run_unavailable')
    response = _envelope(envelope, record['receipt_path'])
    parsed = parse_single_run(response['payload'], record['request_spec'], response['receipt'], row['kickoff'])
    retained = record['measurement']
    _require(parsed == retained, 'retained_weather_differs_from_original_body')
    _require(parsed['requested_run'] == run['manifest']['requested_run'], 'run_initialization_mismatch')
    _require(record['request_spec']['capture_started_at'] == run['manifest']['capture_started_at'], 'weather_capture_identity')
    _require(parsed['venue']['venue_id'] == row['context']['venue_id'], 'weather_venue_identity')
    _require(_time(parsed['received_at']) <= _time(run['manifest']['capture_completed_at']), 'weather_outside_capture')
    winds = parsed['wind_mph_by_hour']
    _require(isinstance(winds, list) and len(winds) == 4, 'four_hour_wind_required')
    winds = [_number(x, 'invalid_hourly_wind', BOUNDS['wind_speed_10m']) for x in winds]
    mean = _number(parsed['wind_mph_four_hour_mean'], 'invalid_mean_wind', BOUNDS['wind_speed_10m'])
    _require(math.isclose(mean, sum(winds) / 4, rel_tol=1e-12, abs_tol=1e-12), 'wind_mean_inconsistent')
    _number(parsed['temperature_f'], 'invalid_temperature', BOUNDS['temperature_2m'])
    _number(parsed['relative_humidity_percent'], 'invalid_humidity', BOUNDS['relative_humidity_2m'])
    return parsed


def _quote(run, row, book, envelope, *, context_received, weather=None):
    matches = [q for q in row.get('quotes', []) if q.get('sportsbook') == book]
    _require(len(matches) == 1, 'unique_' + book + '_main_pair_unavailable')
    quote = matches[0]
    _require(quote.get('quote_id') == digest_json({k: v for k, v in quote.items() if k != 'quote_id'}), 'quote_digest_invalid')
    response = _envelope(envelope, quote['receipt_path'], url=ODDS + '/odds/multi')
    params = response['receipt']['request']['params']
    _require(str(quote['provider_event_id']) in str(params.get('eventIds', '')).split(','), 'quote_not_in_requested_batch')
    _require(set(str(params.get('bookmakers', '')).split(',')) == {'DraftKings', 'FanDuel'}, 'quote_book_request_mismatch')
    rebuilt, _ = parse_quote_pairs(response['payload'], {str(quote['provider_event_id']): row}, response['receipt'])
    rebuilt = [q for q in rebuilt if q['sportsbook'] == book]
    _require(len(rebuilt) == 1 and rebuilt[0] == quote, 'retained_quote_differs_from_unique_original_pair')
    requested, received = _time(quote['requested_at']), _time(quote['observed_at'])
    _require(context_received <= requested <= received <= _time(run['manifest']['capture_completed_at'])
             and received < _time(row['kickoff']), 'quote_context_or_capture_clock')
    if weather is not None:
        _require(row.get('context_recheck_ok') is True, 'context_recheck_unavailable')
        recheck = _envelope(envelope, row['context_recheck_receipt'], url=SUMMARY, params={'event': row['game_id']})
        expected = {k: v for k, v in row['context'].items() if k != 'indoor'}
        _require(summary_identity(recheck['payload'], row) == expected, 'original_context_recheck_mismatch')
        rr = recheck['receipt']
        _require(rr['received_at'] == row['context_recheck_received_at'], 'context_recheck_receipt_mismatch')
        _require(_time(weather['received_at']) <= _time(rr['requested_at']) <= _time(rr['received_at']) <= requested,
                 'weather_recheck_quote_order')
        _require(_time(run['manifest']['weather_stage_completed_at']) <= requested, 'quote_before_weather_stage_completed')
        links = [p for p in row.get('pairs', []) if p.get('quote_id') == quote['quote_id']]
        _require(len(links) == 1, 'unique_weather_quote_link_required')
        link = links[0]
        _require(link.get('context_id') == row['context_id'] and link.get('weather_receipt') == weather['receipt_path']
                 and link.get('quote_receipt') == quote['receipt_path'] and link.get('sportsbook') == book
                 and link.get('context_recheck_receipt') == row['context_recheck_receipt'], 'weather_quote_link_mismatch')
        _require(math.isclose(_number(link['weather_to_quote_seconds'], 'invalid_receipt_gap'),
                             (received - _time(weather['received_at'])).total_seconds(), abs_tol=1e-6), 'weather_quote_gap_mismatch')
    return deepcopy(quote)


def _q(quote):
    # Algebraically equivalent to proportional reciprocal-price normalization,
    # with a ratio form that avoids adding very large finite decimal prices.
    over, under = quote['over_decimal_odds'], quote['under_decimal_odds']
    ratio = under / over
    result = 1 / (1 + ratio)
    _require(math.isfinite(result) and 0 < result < 1, 'price_reference_not_interior')
    return result


def build_observation(decision, old_run, new_run, *, envelope):
    """Build one label-free row from an inventory-designated verified pair."""
    _require(decision.get('source_receipt_verified_input_eligible') is True, 'inventory_designation_unverified')
    gid = _id(decision['game_id'])
    old_source, new_source = _run(old_run), _run(new_run)
    _require(_run_identity(new_run) == (str(decision['run_id']), str(decision['run_attempt']))
             and str(decision['preceding_run_id']) == _run_identity(old_run)[0], 'designation_run_identity')
    _require(_time(decision['capture_started_at']) == _time(new_run['manifest']['capture_started_at']), 'designation_capture_identity')
    gap = (_time(new_source['capture_started_at']) - _time(old_source['capture_started_at'])).total_seconds() / 3600
    _require(4 <= gap <= 8, 'capture_gap_outside_4_8_hours')
    old, old_context_time = _context(old_run, gid, envelope)
    new, new_context_time = _context(new_run, gid, envelope)
    _require(old['context'] == new['context'] and old['context_id'] == new['context_id'] == decision['context_id'], 'changed_context')
    _require(_time(new['kickoff']) == _time(decision['kickoff']), 'designation_kickoff_mismatch')
    start_lead = (_time(new['kickoff']) - _time(new_source['capture_started_at'])).total_seconds() / 3600
    _require(24 <= start_lead <= 48, 'designated_capture_outside_24_48_hours')
    a, b = _weather(old_run, old, envelope), _weather(new_run, new, envelope)
    for key in ('venue', 'valid_hours_utc', 'source_url', 'parser_version', 'model', 'product'):
        _require(a[key] == b[key], 'changed_forecast_' + key)
    options = lambda row: {k: v for k, v in row['single_run']['request_spec']['parameters'].items() if k != 'run'}
    _require(options(old) == options(new), 'changed_forecast_request_options')
    _require(_time(b['requested_run']) - _time(a['requested_run']) == timedelta(hours=6), 'initializations_not_six_hours_apart')
    _require(a['receipt_path'] != b['receipt_path'], 'reused_weather_receipt')
    q0 = _quote(old_run, old, 'draftkings', envelope, context_received=old_context_time, weather=a)
    dk = _quote(new_run, new, 'draftkings', envelope, context_received=new_context_time, weather=b)
    fd = _quote(new_run, new, 'fanduel', envelope, context_received=new_context_time, weather=b)
    cutoff = max(_time(dk['observed_at']), _time(fd['observed_at']))
    _require(_time(q0['observed_at']) < _time(b['received_at']) <= cutoff, 'cross_capture_receipt_order')
    lead = (_time(new['kickoff']) - cutoff).total_seconds() / 3600
    _require(24 <= lead <= 48, 'actual_decision_outside_24_48_hours')
    _require(_time(decision['decision_received_at']) == cutoff, 'inventory_decision_cutoff_mismatch')
    quote_ids = {'t0_draftkings': q0['quote_id'], 't1_draftkings': dk['quote_id'], 't1_fanduel': fd['quote_id']}
    _require(decision['quote_ids'] == quote_ids and decision['weather_receipts'] == [a['receipt_path'], b['receipt_path']], 'inventory_receipt_selection_mismatch')
    p0, p1 = _q(q0), _q(dk)
    logit = lambda p: math.log(p) - math.log1p(-p)
    values = (dk['line'], lead, b['wind_mph_four_hour_mean'], b['temperature_f'], b['relative_humidity_percent'],
              dk['line'] - q0['line'], logit(p1) - logit(p0), fd['line'] - dk['line'],
              b['wind_mph_four_hour_mean'] - a['wind_mph_four_hour_mean'], b['temperature_f'] - a['temperature_f'],
              b['relative_humidity_percent'] - a['relative_humidity_percent'])
    features = {name: _number(value, 'nonfinite_feature:' + name) for name, value in zip(FEATURES, values, strict=True)}
    output = {'schema_version': 'weather-revision-observation-v1', 'adapter_version': VERSION,
              'game_id': gid, 'context_id': new['context_id'], 'context': deepcopy(new['context']),
              'kickoff': new['kickoff'], 'decision_at': _stamp(cutoff), 'features': features,
              'q_under': p1, 'reference_line': dk['line'],
              'probability_input_eligible': dk['line'] % 1 == .5, 'movement_input_eligible': True,
              'observed_offers': [dk, fd], 'quote_ids': quote_ids,
              'weather_receipts': [a['receipt_path'], b['receipt_path']],
              'weather_body_sha256': [a['body_sha256'], b['body_sha256']],
              'older_initialization': a['requested_run'], 'current_initialization': b['requested_run'],
              'capture_gap_hours': gap, 't0': old_source, 't1': new_source}
    output['observation_id'] = digest_json(output)
    return output


def _observation(value):
    _require(value.get('schema_version') == 'weather-revision-observation-v1'
             and value.get('observation_id') == digest_json({k: v for k, v in value.items() if k != 'observation_id'}), 'observation_digest_invalid')
    _require(set(value['features']) == set(FEATURES), 'feature_contract_changed')
    _require(value['reference_line'] == value['features']['market_total'], 'reference_line_mismatch')
    for k, v in value['features'].items():
        _number(v, 'invalid_feature:' + k)
    return value


def final_label(observation, payload, receipt):
    """Extract a final label only after explicit completion and exact identity.

    The payload and receipt must come from the same caller-verified original
    ESPN summary response. Its actual receipt is label availability; a provider
    date, kickoff, or later extraction time cannot replace that observation.
    """
    obs = _observation(observation)
    line = obs['features']['market_total']
    _require(obs['probability_input_eligible'] is True and line > 0 and line % 1 == .5, 'half_point_probability_contract_required')
    r = _receipt(receipt)
    _require(r['request'] == {'url': SUMMARY, 'params': {'event': obs['game_id']}}, 'official_final_receipt_request')
    _require(_time(r['received_at']) > _time(obs['decision_at']), 'final_receipt_predates_decision')
    _require(isinstance(payload, dict), 'official_summary_schema')
    header = payload.get('header', {})
    comps = header.get('competitions', [])
    _require(str(header.get('id')) == obs['game_id'] and len(comps) == 1 and str(comps[0].get('id')) == obs['game_id'], 'final_game_identity')
    c = comps[0]
    status = c.get('status', {}).get('type', {})
    _require(status.get('completed') is True and status.get('state') == 'post'
             and status.get('name') not in {'STATUS_CANCELED', 'STATUS_CANCELLED', 'STATUS_POSTPONED',
                                           'STATUS_SUSPENDED', 'STATUS_ABANDONED'}, 'official_game_not_explicitly_complete')
    context = obs['context']
    # Post-lock context changes are grading flags, never grounds to hide a loss
    # from an otherwise exact game/team final. Observation inputs remain frozen.
    flags = []
    try:
        reported_kickoff = _stamp(c.get('date'))
        if _time(reported_kickoff) != _time(obs['kickoff']):
            flags.append('kickoff_changed')
    except ValueError:
        reported_kickoff = None
        flags.append('reported_kickoff_unavailable')
    if c.get('dateValid') is False:
        flags.append('reported_kickoff_unconfirmed')
    venue = payload.get('gameInfo', {}).get('venue', {})
    reported_venue = str(venue.get('id', '')) or None
    if reported_venue != context['venue_id']:
        flags.append('venue_changed' if reported_venue else 'reported_venue_unavailable')
    if c.get('neutralSite') != context['neutral_site']:
        flags.append('neutral_site_changed' if isinstance(c.get('neutralSite'), bool) else 'reported_neutral_site_unavailable')
    if isinstance(venue.get('indoor'), bool) and venue['indoor'] != context['indoor']:
        flags.append('roof_changed')
    _require(_time(r['requested_at']) >= _time(reported_kickoff or obs['kickoff']), 'final_receipt_not_postgame')
    people = c.get('competitors', [])
    _require(len(people) == 2 and {p.get('homeAway') for p in people} == {'home', 'away'}, 'final_competitor_identity')
    for p in people:
        side = p['homeAway']
        _require(_id(p['team']['id']) == context[side + '_id'], 'final_team_identity')
        if normalize_team(str(p['team'].get('displayName', ''))) != normalize_team(context[side + '_team']):
            flags.append(side + '_reported_name_changed')
    # No score access occurs before every completion/context check above.
    scores = []
    for p in people:
        value = p.get('score')
        if isinstance(value, str):
            _require(value.isascii() and value.isdigit(), 'invalid_final_score')
            value = int(value)
        score = _number(value, 'invalid_final_score')
        _require(score >= 0 and score.is_integer(), 'invalid_final_score')
        scores.append(int(value))
    total = sum(scores)
    return {'schema_version': 'weather-revision-target-v1', 'observation_id': obs['observation_id'],
            'game_id': obs['game_id'], 'context_id': obs['context_id'], 'kickoff': obs['kickoff'],
            'decision_at': obs['decision_at'], 'target_kind': 'final_total', 'final_total': total,
            'label_under': int(total < line), 'target_available_at': r['received_at'],
            'reported_kickoff': reported_kickoff, 'reported_venue_id': reported_venue,
            'context_changed': bool(flags), 'context_flags': sorted(flags),
            'receipt_path': r['receipt_path'], 'body_sha256': r['body_sha256']}


def movement_label(observation, scheduled_runs, *, envelope, complete_enumeration=True):
    """Use the immediate next archived scheduled run, even if it is incomplete."""
    obs = _observation(observation)
    _require(complete_enumeration is True, 'complete_archive_enumeration_required')
    runs = sorted((r for r in scheduled_runs if _scheduled(r)), key=lambda r:
                  (_time(r['manifest']['capture_started_at']), *_run_identity(r)))
    identities = [_run_identity(r) for r in runs]
    _require(len(set(identities)) == len(identities), 'duplicate_scheduled_capture_identity')
    decision_id = obs['t1']['run_id'], obs['t1']['run_attempt']
    _require(identities.count(decision_id) == 1, 'decision_capture_missing_from_enumeration')
    index = identities.index(decision_id)
    _require(runs[index]['path'] == obs['t1']['path'] and runs[index]['sha256'] == obs['t1']['sha256'], 'decision_manifest_changed')
    _require(index + 1 < len(runs), 'immediate_next_scheduled_capture_unavailable')
    next_run = runs[index + 1]  # Never filter by integrity, weather, or quotes first.
    source = _run(next_run)
    row, context_received = _context(next_run, obs['game_id'], envelope)
    _require(row['context_id'] == obs['context_id'] and row['context'] == obs['context'], 'movement_context_changed')
    quote = _quote(next_run, row, 'draftkings', envelope, context_received=context_received)
    hours = (_time(quote['observed_at']) - _time(obs['decision_at'])).total_seconds() / 3600
    _require(4 <= hours <= 8, 'movement_quote_outside_4_8_hours')
    return {'schema_version': 'weather-revision-target-v1', 'observation_id': obs['observation_id'],
            'game_id': obs['game_id'], 'context_id': obs['context_id'], 'kickoff': obs['kickoff'],
            'decision_at': obs['decision_at'], 'target_kind': 'market_movement',
            'movement_points': quote['line'] - obs['features']['market_total'], 'target_line': quote['line'],
            'target_available_at': quote['observed_at'], 'target_quote_id': quote['quote_id'],
            'receipt_path': quote['receipt_path'], 'body_sha256': quote['body_sha256'],
            'target_capture': source, 'decision_to_target_hours': hours}
