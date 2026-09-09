"""Publish aggregate ACC collection health; no HTTP, player parsing or models."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo
import zlib

SCHEMA = 'acc-availability-status-v1'
CAPTURE_SCHEMA = 'acc-availability-collector-v1'
PROTOCOL = 'reports/ACC_AVAILABILITY_CAPTURE_PROTOCOL.md'
START = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 7, tzinfo=timezone.utc)
ZONE = ZoneInfo('America/New_York')
ARCHIVE = Path('model/data/runtime/availability')
PUBLIC = Path('site/data/availability.json')
COUNT_FIELDS = ('source_reports', 'pending_reports', 'nonpending_unverified_reports', 'matched_games',
                'context_verified_games', 'paired_games', 'paired_two_book_games', 'total_requests')
SOURCES = (PROTOCOL, 'ncaaf_model/availability_collector.py', 'ncaaf_model/availability_archive.py',
           'ncaaf_model/revision_archive.py', 'ncaaf_model/weather_revision_collector.py', 'ncaaf_model/teams.py')
REPO = 'https://github.com/drhyphy/ncaa-football-totals/'


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('Explicit timezone required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Explicit timezone required')
    return result.astimezone(timezone.utc)


def iso(value):
    if value.tzinfo is None:
        raise ValueError('Explicit timezone required')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def phase(now):
    iso(now)
    return 'scheduled' if now < START else 'active' if now < END else 'ended'


def slots():
    result, now = [], START
    while now < END:
        local = now.astimezone(ZONE)
        if (local.hour in {0, 1, 2, *range(10, 24)} and local.minute in {17, 47}) or (local.hour == 6 and local.minute == 17):
            result.append(now)
        now += timedelta(minutes=1)
    assert len(result) == 245
    return result


def decision(root, now):
    current = phase(now)
    try:
        previous = json.loads((root / PUBLIC).read_text())
    except (OSError, ValueError):
        previous = {}
    return {'collect': current == 'active', 'publish': current == 'active' or not isinstance(previous, dict)
            or previous.get('schema_version') != SCHEMA or previous.get('phase') != current}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Integrity:
    """Opaque body verification, cached across shared references in this publication."""
    def __init__(self, root):
        self.root = root.resolve()
        self.receipts, self.bodies, self.blobs = {}, {}, {}

    def path(self, value, directory):
        if not isinstance(value, str) or Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('Unsafe archive reference')
        path = self.root / 'model' / value
        allowed = self.root / ARCHIVE / directory
        if not path.resolve().is_relative_to(allowed) or path.resolve().parent != allowed or path.is_symlink():
            raise ValueError('Reference outside availability namespace')
        return path

    def source(self, commit, path, digest):
        if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-f]{64}', str(digest)):
            raise ValueError('Invalid source provenance')
        key = (commit, path)
        if key not in self.blobs:
            body = subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=self.root, stderr=subprocess.DEVNULL)
            self.blobs[key] = hashlib.sha256(body).hexdigest()
        if self.blobs[key] != digest:
            raise ValueError('Recorded source hash mismatch')

    def receipt(self, reference):
        if reference in self.receipts:
            return self.receipts[reference]
        path = self.path(reference, 'receipts')
        data = json.loads(path.read_bytes())
        if not isinstance(data, dict) or data.get('schema_version') not in {'availability-http-receipt-v1', 'raw-http-receipt-v1'}:
            raise ValueError('Unknown receipt schema')
        expected = canonical_hash({k: v for k, v in data.items() if k != 'receipt_path'})
        if data.get('receipt_path') != reference or path.name != expected + '.json':
            raise ValueError('Receipt hash mismatch')
        requested, received = stamp(data.get('requested_at')), stamp(data.get('received_at'))
        if received < requested:
            raise ValueError('Reversed receipt times')
        body_path = data.get('body_path')
        if body_path is not None:
            target = self.path(body_path, 'bodies')
            digest = data.get('body_sha256')
            if not re.fullmatch('[0-9a-f]{64}', str(digest)) or target.name != digest + '.body.gz':
                raise ValueError('Body identity mismatch')
            if body_path not in self.bodies:
                h, size = hashlib.sha256(), 0
                with gzip.open(target, 'rb') as handle:
                    for chunk in iter(lambda: handle.read(65536), b''):
                        h.update(chunk)
                        size += len(chunk)
                self.bodies[body_path] = (h.hexdigest(), size)
            if self.bodies[body_path] != (digest, data.get('body_size_bytes')):
                raise ValueError('Body hash or byte count mismatch')
        elif not data.get('transport_error') and data.get('body_withheld') is not True:
            raise ValueError('Missing body without an explicit failure')
        self.receipts[reference] = data
        return data


def manifest_summary(integrity, path, now):
    body = path.read_bytes()
    data = json.loads(body)
    if not isinstance(data, dict) or data.get('schema_version') != CAPTURE_SCHEMA or data.get('protocol') != PROTOCOL:
        raise ValueError('Unknown availability manifest')
    run, attempt = data.get('run_id'), data.get('run_attempt')
    if any(not isinstance(v, str) or not re.fullmatch('[A-Za-z0-9_-]+', v) for v in (run, attempt)) or path.name != f'{run}-{attempt}.json':
        raise ValueError('Invocation filename mismatch')
    if path.is_symlink() or path.resolve().parent != integrity.root / ARCHIVE / 'runs':
        raise ValueError('Manifest outside namespace')
    started, completed = stamp(data.get('capture_started_at')), stamp(data.get('capture_completed_at'))
    status = data.get('status')
    if status not in {'captured', 'partial', 'failed', 'outside_pilot'} or completed < started or completed > now + timedelta(minutes=5):
        raise ValueError('Invalid capture status/times')
    inside = START <= started < END
    if inside == (status == 'outside_pilot') or data.get('models_fitted') != 0 or data.get('verified_completed_reports') != 0:
        raise ValueError('Capture window or collection-only scope changed')
    counts = data.get('counts')
    if not isinstance(counts, dict) or any(type(counts.get(k)) is not int or counts[k] < 0 for k in COUNT_FIELDS):
        raise ValueError('Invalid aggregate counts')
    counts = {k: counts[k] for k in COUNT_FIELDS}
    if (counts['pending_reports'] + counts['nonpending_unverified_reports'] > counts['source_reports']
            or not 0 <= counts['paired_two_book_games'] <= counts['paired_games'] <= counts['context_verified_games'] <= counts['matched_games'] <= min(20, counts['source_reports'])
            or counts['total_requests'] > 27):
        raise ValueError('Counts do not conserve the fixed cohort')
    references = data.get('receipts')
    if not isinstance(references, list) or any(not isinstance(v, str) for v in references) or len(set(references)) != len(references) or len(references) != counts['total_requests']:
        raise ValueError('Receipt counts disagree')
    if not inside and (any(counts.values()) or references or data.get('reports') or data.get('rows')):
        raise ValueError('Outside-window attempt contains observations')
    if inside:
        hashes = data.get('source_hashes', {})
        if not isinstance(hashes, dict) or set(hashes) != set(SOURCES):
            raise ValueError('Missing source provenance')
        for name, digest in hashes.items():
            integrity.source(data.get('git_commit'), 'model/' + name, digest)
        integrity.source(data.get('git_commit'), '.github/workflows/availability.yml', data.get('workflow_sha256'))
    failed_requests = 0
    for reference in references:
        receipt = integrity.receipt(reference)
        if stamp(receipt['requested_at']) < started or stamp(receipt['received_at']) > completed:
            raise ValueError('Receipt outside capture times')
        failed_requests += receipt.get('status_code') != 200 or receipt.get('body_complete', True) is not True or any(receipt.get(k) for k in ('transport_error', 'capture_error', 'parse_error', 'http_error'))
    if data.get('current_receipt') is not None and data['current_receipt'] not in references:
        raise ValueError('Current report reference is not an archived receipt')
    if data.get('cohort_path') is not None:
        cohort_path = integrity.path(data['cohort_path'], 'cohorts')
        cohort_bytes = cohort_path.read_bytes()
        if hashlib.sha256(cohort_bytes).hexdigest() != data.get('cohort_sha256'):
            raise ValueError('Cohort hash mismatch')
        cohort = json.loads(cohort_bytes)
        if not isinstance(cohort, dict) or cohort.get('inventory_receipt') not in references:
            raise ValueError('Cohort inventory reference missing')
    trigger = data.get('trigger')
    if not isinstance(trigger, str) or trigger not in {'schedule', 'workflow_dispatch', 'manual_local'}:
        raise ValueError('Unknown collection trigger')
    counts['failed_requests'] = int(failed_requests)
    return {'run_id': run, 'run_attempt': attempt, 'trigger': trigger, 'status': status,
            'capture_started_at': iso(started), 'capture_completed_at': iso(completed), 'counts': counts,
            'manifest_sha256': hashlib.sha256(body).hexdigest(), 'references_hash_verified': True}


def build_status(root, now, outcome='skipped', attempt_started_at=None, expected_run_id=None, expected_run_attempt=None):
    current = phase(now)
    integrity, runs, invalid = Integrity(root), [], 0
    for path in sorted((root / ARCHIVE / 'runs').glob('*.json')):
        try:
            runs.append(manifest_summary(integrity, path, now))
        except (OSError, ValueError, TypeError, KeyError, EOFError, RecursionError, zlib.error, subprocess.CalledProcessError):
            invalid += 1
    runs.sort(key=lambda r: (r['capture_started_at'], r['capture_completed_at'], r['run_id'], r['run_attempt']))
    latest = runs[-1] if runs else None
    matched = [r for r in runs if attempt_started_at is not None and stamp(r['capture_started_at']) >= attempt_started_at
               and (expected_run_id is None or r['run_id'] == expected_run_id)
               and (expected_run_attempt is None or r['run_attempt'] == expected_run_attempt)]
    failed = (bool(invalid) or outcome in {'failure', 'cancelled'} or
              (attempt_started_at is not None and (outcome != 'success' or len(matched) != 1 or matched[0]['status'] in {'partial', 'failed'})) or
              (current == 'active' and ((latest is not None and latest['status'] in {'partial', 'failed'}) or (outcome == 'success' and latest is None))))
    status = current if current != 'active' else 'attention' if failed else 'awaiting_first_capture' if latest is None else 'current'
    if status == 'current' and now - stamp(latest['capture_completed_at']) > timedelta(hours=4):
        status = 'stale'
    message = {'scheduled': 'The fixed collection window has not started.', 'ended': 'The fixed pilot has ended; automatic source and price requests have stopped.',
               'attention': 'Collection needs attention. Available original receipts and failures are retained.',
               'awaiting_first_capture': 'The pilot is awaiting its first archived collection.',
               'stale': 'No completed capture receipt in the past four hours. GitHub jobs can be delayed or missed.',
               'current': 'Recent collection receipts are available; captured transport is not a verified completed report.'}[status]
    inside = [r for r in runs if r['status'] != 'outside_pilot']
    totals = {key: sum(r['counts'][key] for r in inside) for key in (*COUNT_FIELDS, 'failed_requests')}
    grid = slots()
    return {'schema_version': SCHEMA, 'generated_at': iso(now), 'phase': current, 'status': status, 'message': message,
            'pilot_start': iso(START), 'pilot_end': iso(END), 'timezone': 'America/New_York', 'registered_slots': len(grid),
            'schedule': {'minutes': [17, 47], 'hours': [0, 1, 2, *range(10, 24)], 'additional': '06:17'},
            'nominal_slots_elapsed': sum(t <= now for t in grid), 'first_slot': iso(grid[0]), 'last_slot': iso(grid[-1]),
            'archived_runs': len(runs), 'archived_scheduled_attempts': sum(r['trigger'] == 'schedule' for r in inside),
            'archived_manual_attempts': sum(r['trigger'] in {'workflow_dispatch', 'manual_local'} for r in inside),
            'outside_window_attempts': len(runs)-len(inside), 'invalid_manifests': invalid,
            'partial_or_failed_runs': sum(r['status'] in {'partial', 'failed'} for r in inside), 'latest': latest,
            'observation_totals': totals, 'verified_completed_reports': 0, 'models_fitted': 0,
            'collection_only': True, 'performance_evaluated': False, 'active_policy_changed': False, 'edge_established': False,
            'last_workflow_attempt': {'started_at': iso(attempt_started_at), 'outcome': outcome, 'matching_manifest': len(matched) == 1} if attempt_started_at else None,
            'counting_note': 'Counts are repeated observations and archived attempts, not distinct reports, games or bets. Actual starts do not identify every missed or delayed nominal slot; unarchived failures are not counted as completed captures.',
            'report_note': 'Pending and nonpending reports remain completion-unverified. Empty rows do not mean players are available; source issue labels differ from UTC receipt times. No player semantics, model fit or betting edge has been established.',
            'links': [{'name': 'Fixed collection protocol', 'url': REPO+'blob/main/model/'+PROTOCOL},
                      {'name': 'Original public archives', 'url': REPO+'tree/main/'+ARCHIVE.as_posix()}]}, bool(failed)


def write_public(root, data):
    path = root / PUBLIC
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def emit(name, value):
    value = ('true' if value else 'false') if type(value) is bool else str(value)
    line = f'{name}={value}'
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(line+'\n')
    print(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['decision', 'publish'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--collector-outcome', choices=['success', 'failure', 'skipped', 'cancelled', ''], default='skipped')
    parser.add_argument('--attempt-started-at', default='')
    parser.add_argument('--expected-run-id', default=os.getenv('GITHUB_RUN_ID'))
    parser.add_argument('--expected-run-attempt', default=os.getenv('GITHUB_RUN_ATTEMPT'))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.command == 'decision':
        result = decision(args.root, now)
        for name, value in result.items():
            emit(name, value)
        emit('attempt_started_at', iso(now) if result['collect'] else '')
    else:
        attempt = stamp(args.attempt_started_at) if args.attempt_started_at else None
        data, failed = build_status(args.root, now, args.collector_outcome, attempt, args.expected_run_id, args.expected_run_attempt)
        write_public(args.root, data)
        emit('collection_failed', failed)


if __name__ == '__main__':
    main()
