"""Bounded original-response label collection, separate from feature capture.

All roots are repository roots; HTTP receipt/body paths are model-relative.
No requests occur on import. Finals and corrections are append-only; label
availability is always the original response receipt, never this reader's time.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'model'))

from ncaaf_model import weather_revision_dataset as dataset
from ncaaf_model.revision_archive import ArchiveClient, digest_json, immutable_json, load_envelope, timestamp
from ncaaf_model.weather_revision_collector import SUMMARY
from ncaaf_model.weather_revision_study import REPORT_AT

VERSION = 'weather-revision-study-labels-v1'
BASE = Path('model/data/runtime/weather_revision_study')
MAX_FINAL_CALLS = 24
PENDING_CADENCE = timedelta(hours=6)
CORRECTION_CADENCE = timedelta(hours=24)
KINDS = ('final_total', 'market_movement')


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _time(value):
    return dataset._time(value)


def _iso(value):
    return dataset._stamp(value)


def _read_json(path):
    return json.loads(path.read_text())


def _safe_path(root, relative, directory):
    _require(isinstance(relative, str), 'missing_archive_path')
    path = (root / relative).resolve()
    _require(path.is_relative_to((root / directory).resolve()), 'archive_path_outside_namespace')
    return path


def load_study_envelope(root, receipt_path):
    """Verify a custom study HTTP archive without changing the frozen writer."""
    model = Path(root).resolve() / 'model'
    base = Path('data/runtime/weather_revision_study/http')
    path = _safe_path(model, receipt_path, base / 'receipts')
    receipt = _read_json(path)
    _require(receipt.get('schema_version') == 'raw-http-receipt-v1'
             and receipt.get('receipt_path') == receipt_path, 'study_receipt_identity')
    _require(path.name == digest_json({k: v for k, v in receipt.items() if k != 'receipt_path'}) + '.json', 'study_receipt_digest')
    _require(_time(receipt['requested_at']) <= _time(receipt['received_at']), 'study_receipt_clock_order')
    payload = None
    if receipt.get('body_path'):
        body = gzip.decompress(_safe_path(model, receipt['body_path'], base / 'bodies').read_bytes())
        _require(hashlib.sha256(body).hexdigest() == receipt['body_sha256']
                 and len(body) == receipt['body_size_bytes'], 'study_body_hash_or_size')
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            _require(bool(receipt.get('transport_error')), 'unrecorded_invalid_response_json')
    else:
        _require(bool(receipt.get('transport_error')) or receipt.get('body_withheld') is True, 'missing_response_body')
    return {'payload': payload, 'receipt': receipt}


def _observation(root, observation_id):
    dataset._hash(observation_id)
    path = Path(root) / BASE / 'observations' / (observation_id + '.json')
    value = _read_json(path)
    dataset._observation(value)
    _require(value['observation_id'] == observation_id, 'stored_observation_identity')
    return value


def _verify_movement(root, observation, label):
    """Recheck the pinned target capture and original price/context responses.

    Complete chronological next-run selection is performed before persistence
    by movement_label. This read-time check verifies its pinned source target;
    it does not replace that target using a newer archive enumeration.
    """
    source = label['target_capture']
    path = _safe_path(Path(root), source['path'], Path('model/data/runtime/weather_revisions/runs'))
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == source['sha256'], 'movement_manifest_hash')
    manifest = json.loads(raw)
    for key in ('run_id', 'run_attempt', 'capture_started_at', 'capture_completed_at', 'git_commit'):
        _require(str(manifest.get(key)) == str(source.get(key)), 'movement_manifest_identity')
    _require(manifest.get('provenance', {}) == source['source_files_sha256'], 'movement_source_references')
    model = Path(root) / 'model'
    cpath = _safe_path(model, manifest['cohort_path'], Path('data/runtime/weather_revisions/cohorts'))
    craw = cpath.read_bytes()
    _require(hashlib.sha256(craw).hexdigest() == source['cohort_sha256'], 'movement_cohort_hash')
    rows = manifest['rows']
    _require(len({str(r['game_id']) for r in rows}) == len(rows), 'duplicate_movement_manifest_game')
    run = {'manifest': manifest, 'cohort': json.loads(craw), 'rows': {str(r['game_id']): r for r in rows}}
    envelope = lambda relative: load_envelope(model, relative)
    row, context_received = dataset._context(run, observation['game_id'], envelope)
    _require(row['context'] == observation['context'] and row['context_id'] == observation['context_id'], 'movement_context_changed')
    quote = dataset._quote(run, row, 'draftkings', envelope, context_received=context_received)
    gap = (_time(quote['observed_at']) - _time(observation['decision_at'])).total_seconds() / 3600
    _require(4 <= gap <= 8 and label['decision_to_target_hours'] == gap, 'movement_target_clock')
    _require(label['target_line'] == quote['line']
             and label['movement_points'] == quote['line'] - observation['reference_line']
             and label['target_available_at'] == quote['observed_at']
             and label['target_quote_id'] == quote['quote_id']
             and label['receipt_path'] == quote['receipt_path']
             and label['body_sha256'] == quote['body_sha256'], 'movement_target_value_or_receipt')


def _validate_label(root, label):
    _require(label.get('schema_version') == 'weather-revision-target-v1'
             and label.get('target_kind') in KINDS, 'label_schema')
    obs = _observation(root, label['observation_id'])
    _require(all(label.get(k) == obs.get(k) for k in ('observation_id', 'game_id', 'context_id', 'kickoff', 'decision_at')), 'label_observation_identity')
    _require(_time(label['target_available_at']) > _time(obs['decision_at']), 'label_predates_decision')
    if label['target_kind'] == 'final_total':
        original = load_study_envelope(root, label['receipt_path'])
        _require(dataset.final_label(obs, original['payload'], original['receipt']) == label, 'final_label_differs_from_original')
    else:
        _verify_movement(root, obs, label)
    return label


def _label_records(root):
    records, errors = [], []
    for path in sorted((Path(root) / BASE / 'labels').glob('*.json')):
        try:
            value = _read_json(path)
            _require(path.name == digest_json(value) + '.json', 'label_file_digest')
            records.append(_validate_label(root, value))
        except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
            errors.append({'reason': 'invalid_label_archive', 'path': path.relative_to(root).as_posix(),
                           'exception_class': type(exc).__name__})
    return records, errors


def _semantic(label):
    keys = ('game_id', 'context_id', 'kickoff', 'decision_at', 'target_kind')
    values = {key: label[key] for key in keys}
    if label['target_kind'] == 'final_total':
        keys = ('final_total', 'label_under', 'reported_kickoff', 'reported_venue_id', 'context_changed', 'context_flags')
    else:
        keys = ('movement_points', 'target_line')
    values.update({key: label.get(key) for key in keys})
    return digest_json(values)


def load_latest_labels(root, *, cutoff, inclusive=False):
    """Latest valid receipt per target/observation; conflicting latest ties fail.

    Equivalent same-time labels can share a semantic value but have separate
    original receipts; choose the smallest content digest only in that case.
    Any corrupt persisted record makes the archive unavailable for this read.
    """
    _require(type(inclusive) is bool, 'inclusive_must_be_boolean')
    root, cutoff = Path(root).resolve(), _time(cutoff)
    records, errors = _label_records(root)
    result = {kind: {} for kind in KINDS}
    result['errors'] = errors
    if errors:
        return result
    groups = defaultdict(list)
    for label in records:
        at = _time(label['target_available_at'])
        if at < cutoff or (inclusive and at == cutoff):
            groups[label['target_kind'], label['observation_id']].append(label)
    for (kind, oid), labels in sorted(groups.items()):
        newest = max(_time(label['target_available_at']) for label in labels)
        tied = [label for label in labels if _time(label['target_available_at']) == newest]
        if len({_semantic(label) for label in tied}) != 1:
            result['errors'].append({'reason': 'ambiguous_same_timestamp_labels', 'target_kind': kind,
                                     'observation_id': oid, 'target_available_at': _iso(newest),
                                     'label_sha256': sorted(digest_json(label) for label in tied)})
            continue
        result[kind][oid] = min(tied, key=digest_json)
    return result


def _attempts(root):
    values, errors = [], []
    for path in sorted((root / BASE / 'label_attempts').glob('*.json')):
        try:
            value = _read_json(path)
            _require(path.name == digest_json(value) + '.json'
                     and value.get('schema_version') == 'weather-revision-label-attempt-v1', 'attempt_identity')
            dataset._hash(value['observation_id'])
            dataset._id(value['game_id'])
            _require(_time(value['requested_at']) <= _time(value['received_at']), 'attempt_clock_order')
            if value.get('receipt_path'):
                original = load_study_envelope(root, value['receipt_path'])['receipt']
                _require(all(original.get(k) == value.get(k) for k in ('requested_at', 'received_at', 'status_code', 'body_sha256')), 'attempt_receipt_mismatch')
            else:
                _require(value.get('status') == 'request_not_archived' and value.get('operational_clock_only') is True,
                         'missing_attempt_receipt')
            values.append(value)
        except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
            errors.append({'reason': 'invalid_poll_attempt_archive', 'path': path.relative_to(root).as_posix(),
                           'exception_class': type(exc).__name__})
    return values, errors


def _store_label(root, label):
    # Reconstruct from original archived values again at the persistence edge.
    _validate_label(root, label)
    path = root / BASE / 'labels' / (digest_json(label) + '.json')
    existed = path.exists()
    immutable_json(path, label)
    return not existed


def collect_labels(root, observations, runs, *, envelope, now, client=None, complete_enumeration=True):
    """Collect available original labels, with at most 24 final HTTP calls.

    Final polls start strictly before REPORT_AT; pending games wait at least six
    hours between attempts, and previously finalized games at least 24 hours.
    Last successful or failed actual poll receipt controls cadence. Never-polled
    games come first, then oldest last-poll time, kickoff, and numeric game ID.
    These are minimum intervals under a bounded fair queue, not promised SLAs.
    """
    root, now = Path(root).resolve(), _time(now)
    result = {'schema_version': 'weather-revision-label-collection-v1', 'version': VERSION,
              'status': 'ok', 'records': [], 'attempts': [], 'errors': [], 'unavailable_targets': [],
              'counts': {'observations': 0, 'final_http_calls': 0, 'labels_written': 0,
                         'final_labels_available': 0, 'movement_labels_available': 0,
                         'movement_targets_unavailable': 0, 'final_targets_pending': 0,
                         'final_due_games': 0, 'final_deferred_by_cap': 0}}
    selected, games = {}, {}
    try:
        for observation in observations:
            dataset._observation(observation)
            oid, gid = observation['observation_id'], dataset._id(observation['game_id'])
            _require(gid not in games or games[gid] == oid, 'multiple_observations_for_game')
            _require(oid not in selected or selected[oid] == observation, 'conflicting_observation_identity')
            selected[oid], games[gid] = observation, oid
        for oid, observation in selected.items():
            immutable_json(root / BASE / 'observations' / (oid + '.json'), observation)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        result['status'] = 'failed'
        result['errors'].append({'reason': 'invalid_observation_archive', 'exception_class': type(exc).__name__})
        return result
    result['counts']['observations'] = len(selected)
    if not selected:
        result['status'] = 'no_observations'
        return result
    previous, errors = _label_records(root)
    attempts, attempt_errors = _attempts(root)
    result['errors'].extend(errors + attempt_errors)
    if result['errors']:
        result['status'] = 'failed'
        return result
    finalized = {label['observation_id'] for label in previous
                 if label['target_kind'] == 'final_total' and _time(label['target_available_at']) <= now}
    last_poll = {}
    for attempt in attempts:
        oid = attempt['observation_id']
        last_poll[oid] = max(last_poll.get(oid, datetime.min.replace(tzinfo=timezone.utc)), _time(attempt['received_at']))
    # Movement coverage cannot block independently available final-label polling.
    for oid, observation in sorted(selected.items()):
        try:
            label = dataset.movement_label(observation, runs, envelope=envelope, complete_enumeration=complete_enumeration)
        except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
            item = {'reason': 'movement_target_unavailable', 'target_kind': 'market_movement',
                    'observation_id': oid, 'exception_class': type(exc).__name__}
            corrupt = isinstance(exc, (OSError, EOFError)) or str(exc).startswith(('Archived receipt hash', 'Archived body hash', 'Immutable archive'))
            if corrupt:
                result['errors'].append({**item, 'reason': 'movement_source_archive_invalid'})
            else:
                result['unavailable_targets'].append(item)
                result['counts']['movement_targets_unavailable'] += 1
            continue
        try:
            result['counts']['labels_written'] += int(_store_label(root, label))
            result['counts']['movement_labels_available'] += 1
            result['records'].append(label)
        except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
            result['errors'].append({'reason': 'movement_label_persistence_invalid', 'observation_id': oid,
                                     'exception_class': type(exc).__name__})
    due = []
    if now < REPORT_AT:
        for oid, observation in selected.items():
            if observation.get('probability_input_eligible') is not True or observation['reference_line'] % 1 != .5:
                continue
            if oid not in finalized and _time(observation['kickoff']) + timedelta(hours=6) > now:
                continue
            last = last_poll.get(oid)
            cadence = CORRECTION_CADENCE if oid in finalized else PENDING_CADENCE
            if last is not None and now - last < cadence:
                continue
            due.append(observation)
        due.sort(key=lambda o: (last_poll.get(o['observation_id'], datetime.min.replace(tzinfo=timezone.utc)),
                               _time(o['kickoff']), int(o['game_id']), o['observation_id']))
    result['counts']['final_due_games'] = len(due)
    result['counts']['final_deferred_by_cap'] = max(0, len(due) - MAX_FINAL_CALLS)
    cursor = now
    for observation in due[:MAX_FINAL_CALLS]:
        if max(cursor, _time(timestamp())) >= REPORT_AT:
            result['unavailable_targets'].append({'reason': 'final_poll_deadline_reached', 'target_kind': 'final_total',
                                                  'observation_id': observation['observation_id']})
            break
        if client is None:
            client = ArchiveClient(root / 'model', archive=root / BASE / 'http')
        result['counts']['final_http_calls'] += 1
        request_started = timestamp()
        try:
            response = client.fetch(SUMMARY, {'event': observation['game_id']}, purpose='weather_revision_official_final')
            receipt = response['receipt']
            _time(receipt['received_at'])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            attempt = {'schema_version': 'weather-revision-label-attempt-v1', 'version': VERSION,
                       'observation_id': observation['observation_id'], 'game_id': observation['game_id'],
                       'status': 'request_not_archived', 'requested_at': request_started, 'received_at': timestamp(),
                       'receipt_path': None, 'status_code': None, 'body_sha256': None, 'label_sha256': None,
                       'operational_clock_only': True, 'exception_class': type(exc).__name__,
                       'previously_finalized': observation['observation_id'] in finalized}
            immutable_json(root / BASE / 'label_attempts' / (digest_json(attempt) + '.json'), attempt)
            result['attempts'].append(attempt)
            result['errors'].append({'reason': 'final_request_not_archived', 'observation_id': observation['observation_id'],
                                     'exception_class': type(exc).__name__})
            break
        cursor = max(cursor, _time(receipt['received_at']))
        attempt = {'schema_version': 'weather-revision-label-attempt-v1', 'version': VERSION,
                   'observation_id': observation['observation_id'], 'game_id': observation['game_id'],
                   'status': 'unavailable', **{k: receipt.get(k) for k in
                       ('requested_at', 'received_at', 'receipt_path', 'status_code', 'body_sha256')},
                   'previously_finalized': observation['observation_id'] in finalized,
                   'label_sha256': None, 'exception_class': None}
        try:
            # Never trust a fake/decoded payload disconnected from the archived
            # bytes: load the original custom-archive receipt before parsing.
            original = load_study_envelope(root, receipt['receipt_path'])
            _require(original['receipt'] == receipt, 'returned_receipt_differs_from_archive')
            label = dataset.final_label(observation, original['payload'], original['receipt'])
            result['counts']['labels_written'] += int(_store_label(root, label))
            result['counts']['final_labels_available'] += 1
            result['records'].append(label)
            attempt['status'], attempt['label_sha256'] = 'labeled', digest_json(label)
        except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
            attempt['exception_class'] = type(exc).__name__
            failed_request = receipt.get('status_code') != 200 or receipt.get('transport_error')
            pending = not failed_request and isinstance(exc, ValueError) and str(exc) == 'official_game_not_explicitly_complete'
            attempt['status'] = 'request_failed' if failed_request else ('pending_final' if pending else 'invalid_final_source')
            item = {'target_kind': 'final_total', 'observation_id': observation['observation_id'],
                    'receipt_path': receipt['receipt_path'], 'exception_class': type(exc).__name__}
            if pending:
                result['unavailable_targets'].append({**item, 'reason': 'official_final_pending'})
                result['counts']['final_targets_pending'] += 1
            else:
                result['errors'].append({**item, 'reason': 'final_source_request_failed' if failed_request else 'final_source_validation_failed'})
        immutable_json(root / BASE / 'label_attempts' / (digest_json(attempt) + '.json'), attempt)
        result['attempts'].append(attempt)
        if receipt.get('status_code') in {401, 403, 429}:
            result['errors'].append({'reason': 'official_source_access_limited', 'status_code': receipt['status_code']})
            break
    if result['errors']:
        result['status'] = 'partial'
    elif now >= REPORT_AT:
        result['status'] = 'past_report_cutoff'
    return result
