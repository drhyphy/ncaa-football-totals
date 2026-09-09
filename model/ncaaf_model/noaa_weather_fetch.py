"""Immutable NOAA inventory and byte-range download stages; no outcome analysis.

The existing request plan remains untouched. --inventory must finish and freeze
exact ranges before --fetch can run. Each stage uses at most four workers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

import pandas as pd
import requests

from . import noaa_weather_research as planning

VERSION = 'noaa-original-forecast-fetch-v1'
RAW = planning.RAW
INVENTORY = RAW / 'inventory_plan.json'
FETCH_MANIFEST = RAW / 'fetch_manifest.json'
MAX_INDEX_BYTES = 2_000_000
MAX_FIELD_BYTES = 16_000_000
_LOCAL = threading.local()


class SourceExcluded(ValueError):
    """A documented terminal source exclusion, never a later-cycle fallback."""


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text())


def immutable_bytes(path, payload):
    """Atomic create, or verify an identical existing file; never overwrite."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError('Immutable archive conflict: '+path.name)
    finally:
        temporary.unlink(missing_ok=True)


def immutable_json(path, value):
    immutable_bytes(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode())


def progress(root, stage, value):
    path = root / 'reports' / f'noaa_weather_{stage}_status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


@contextmanager
def stage_lock(root, stage):
    path = root / RAW / f'.{stage}.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('This NOAA stage is already running') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def session():
    if not hasattr(_LOCAL, 'session'):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers.update({'User-Agent': 'NCAAF-public-original-forecast-research/1.0',
                                       'Accept-Encoding': 'identity'})
    return _LOCAL.session


def header_time(value):
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            raise ValueError('Timezone absent')
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError) as exc:
        raise SourceExcluded('missing_or_invalid_last_modified') from exc


def validate_head(headers, deadline):
    h = requests.structures.CaseInsensitiveDict(headers)
    modified = header_time(h.get('Last-Modified'))
    if modified >= pd.Timestamp(deadline).to_pydatetime():
        raise SourceExcluded('object_last_modified_not_before_decision')
    tag = h.get('ETag', '')
    if not tag.startswith('"') or not tag.endswith('"') or tag.startswith('W/'):
        raise SourceExcluded('missing_or_weak_object_etag')
    try:
        size = int(h['Content-Length'])
    except (KeyError, ValueError) as exc:
        raise SourceExcluded('missing_object_length') from exc
    if size <= 16:
        raise SourceExcluded('invalid_object_length')
    if h.get('Content-Encoding', 'identity').lower() != 'identity':
        raise SourceExcluded('encoded_object_not_byte_addressable')
    return {'etag': tag, 'last_modified': modified.isoformat(), 'object_bytes': size,
            'etag_multipart_pattern': bool(re.fullmatch(r'"[0-9a-fA-F]{32}-[0-9]+"', tag)),
            'timing_evidence_class': 'initialization_buffer_and_s3_last_modified_availability_proxy_not_certified_publication'}


def parse_inventory(raw, request, object_bytes):
    """Match exact selectors and complete-message offsets, including final row."""
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise SourceExcluded('inventory_not_utf8') from exc
    rows = []
    for line in lines:
        pieces = line.split(':')
        if len(pieces) < 6 or not pieces[0].isdigit() or not pieces[1].isdigit():
            raise SourceExcluded('malformed_inventory_row')
        rows.append({'offset': int(pieces[1]), 'cycle': pieces[2],
                     'selector': pieces[3]+':'+pieces[4], 'lead': pieces[5], 'line': line})
    if not rows or rows[0]['offset'] != 0:
        raise SourceExcluded('inventory_missing_initial_offset')
    offsets = [row['offset'] for row in rows]
    if any(b <= a for a, b in zip(offsets, offsets[1:])) or offsets[-1] >= object_bytes:
        raise SourceExcluded('inventory_offsets_not_strictly_increasing')
    cycle = 'd='+pd.Timestamp(request['initialization']).strftime('%Y%m%d%H')
    lead_text = f"{request['lead_hours']} hour fcst"
    ranges = []
    for field in request['fields']:
        selector = planning.FIELD_SELECTORS[field]
        matches = [(i, row) for i, row in enumerate(rows) if row['selector'] == selector]
        if len(matches) != 1:
            raise SourceExcluded('missing_or_ambiguous_field:'+field)
        index, row = matches[0]
        if row['cycle'] != cycle or row['lead'] != lead_text:
            raise SourceExcluded('wrong_inventory_initialization_or_lead:'+field)
        end = offsets[index+1]-1 if index+1 < len(offsets) else object_bytes-1
        size = end-row['offset']+1
        if not 20 <= size <= MAX_FIELD_BYTES:
            raise SourceExcluded('field_message_size_outside_bound:'+field)
        ranges.append({'field': field, 'selector': selector, 'start': row['offset'],
                       'end': end, 'bytes': size, 'inventory_line': row['line']})
    return ranges


def limited_body(response, maximum):
    chunks, size = [], 0
    for chunk in response.iter_content(chunk_size=262144):
        size += len(chunk)
        if size > maximum:
            raise ValueError('Response exceeded locked byte limit')
        chunks.append(chunk)
    return b''.join(chunks)


def inventory_one(root, plan_sha, request, client=None):
    root = Path(root)
    path = root / RAW / 'inventory' / (request['request_id']+'.json')
    if path.exists():
        record = read_json(path)
        if record['request_sha256'] != planning.digest(request) or record['parent_plan_sha256'] != plan_sha:
            raise ValueError('Cached inventory is for a different request plan')
        if record.get('index_path') and planning.sha(root/record['index_path']) != record['index_sha256']:
            raise ValueError('Cached inventory bytes changed')
        return record
    client = client or session()
    record = {'version': VERSION, 'request_id': request['request_id'],
              'request_sha256': planning.digest(request), 'parent_plan_sha256': plan_sha,
              'source_url': request['source_url'], 'index_url': request['index_url'],
              'observed_at': now(), 'status': 'excluded', 'ranges': []}
    try:
        response = client.head(request['source_url'], timeout=(10, 45), allow_redirects=False)
        try:
            record['head_status'] = response.status_code
            record['head_headers'] = dict(response.headers)
            if response.status_code == 404:
                raise SourceExcluded('forecast_object_not_found')
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected HEAD status')
            record.update(validate_head(response.headers, request['last_modified_must_be_strictly_before_utc']))
        finally:
            response.close()
        response = client.get(request['index_url'], timeout=(10, 45), stream=True,
                              allow_redirects=False, headers={'Accept-Encoding': 'identity'})
        try:
            record['index_status'] = response.status_code
            record['index_headers'] = dict(response.headers)
            if response.status_code == 404:
                raise SourceExcluded('forecast_inventory_not_found')
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected inventory status')
            raw = limited_body(response, MAX_INDEX_BYTES)
        finally:
            response.close()
        index_path = RAW / 'inventory' / (request['request_id']+'.idx')
        immutable_bytes(root/index_path, raw)
        record.update(index_path=str(index_path), index_sha256=hashlib.sha256(raw).hexdigest())
        record['ranges'] = parse_inventory(raw, request, record['object_bytes'])
        record['status'] = 'available'
    except SourceExcluded as exc:
        record['reason'] = str(exc)
    immutable_json(path, record)
    return record


def _failure(root, stage, item, exc):
    detail = {'version': VERSION, 'stage': stage, 'observed_at': now(),
              'item_id': item, 'exception': type(exc).__name__, 'reason': str(exc),
              'status': 'incomplete_retryable_or_requires_integrity_review'}
    path = root/RAW/(stage+'_attempts')/(item+'-'+uuid.uuid4().hex+'.json')
    immutable_json(path, detail)
    return detail


def _parallel(root, stage, entries, worker, workers):
    if not 1 <= workers <= 4:
        raise ValueError('Workers must be between1 and4')
    results, failures = {}, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(worker, entry): entry for entry in entries}
        for count, future in enumerate(as_completed(pending), 1):
            entry = pending[future]
            item = entry.get('range_id') or entry['request_id']
            try:
                results[item] = future.result()
            except Exception as exc:  # Preserve every failure; no invented fallback.
                failures.append(_failure(root, stage, item, exc))
            if count % 25 == 0 or count == len(pending):
                print(json.dumps({'stage': stage, 'completed_this_run': count,
                                  'requested_this_run': len(pending), 'failures': len(failures)}), flush=True)
    return results, failures


def inventory(root, *, workers=4, max_objects=None):
    root = Path(root)
    with stage_lock(root, 'inventory'):
        if (root/INVENTORY).exists():
            return read_inventory(root)
        plan = planning.read_plan(root)
        pending = [r for r in plan['requests'] if not (root/RAW/'inventory'/(r['request_id']+'.json')).exists()]
        if max_objects is not None:
            pending = pending[:max_objects]
        _, failures = _parallel(root, 'inventory', pending,
            lambda r: inventory_one(root, plan['plan_sha256'], r), workers)
        records, missing, integrity_errors = [], [], []
        for request in plan['requests']:
            path = root/RAW/'inventory'/(request['request_id']+'.json')
            if not path.exists():
                missing.append(request['request_id'])
                continue
            try:
                record = inventory_one(root, plan['plan_sha256'], request)
                records.append((request, record, path))
            except Exception as exc:
                integrity_errors.append(_failure(root, 'inventory', request['request_id'], exc))
        status = {'version': VERSION, 'parent_plan_sha256': plan['plan_sha256'], 'observed_at': now(),
                  'complete': not missing and not integrity_errors, 'planned_objects': len(plan['requests']),
                  'available_objects': sum(r['status']=='available' for _, r, _ in records),
                  'excluded_objects': sum(r['status']=='excluded' for _, r, _ in records),
                  'missing_objects': missing, 'failures_this_run': failures+integrity_errors}
        progress(root, 'inventory', status)
        if not status['complete']:
            return status
        ranges, objects, source_hashes = [], [], {}
        for request, record, path in records:
            source_hashes[str(path.relative_to(root))] = planning.sha(path)
            if record.get('index_path'):
                source_hashes[record['index_path']] = record['index_sha256']
            objects.append({'request_id': request['request_id'], 'status': record['status'],
                'reason': record.get('reason'), 'record_path': str(path.relative_to(root)),
                'game_ids': [g['game_id'] for g in request['games']]})
            for span in record['ranges']:
                item = {**span, 'request_id': request['request_id'], 'source_url': request['source_url'],
                    'etag': record['etag'], 'last_modified': record['last_modified'],
                    'etag_multipart_pattern': record['etag_multipart_pattern'],
                    'timing_evidence_class': record['timing_evidence_class'],
                    'object_bytes': record['object_bytes'], 'initialization': request['initialization'],
                    'lead_hours': request['lead_hours'],
                    'last_modified_must_be_strictly_before_utc': request['last_modified_must_be_strictly_before_utc']}
                item['range_id'] = planning.digest(item)[:32]
                ranges.append(item)
        code_path = RAW/'frozen_fetch_source.py'
        immutable_bytes(root/code_path, Path(__file__).read_bytes())
        source_hashes[str(code_path)] = planning.sha(root/code_path)
        result = {**status, 'source_files_sha256': source_hashes, 'objects': objects, 'ranges': ranges,
            'exact_field_bytes': sum(r['bytes'] for r in ranges), 'field_range_requests': len(ranges),
            'index_bytes': sum((root/r['index_path']).stat().st_size for _, r, _ in records if r.get('index_path')),
            'weather_values_classified': False, 'outcomes_used': False,
            'timing_limitation': 'S3 Last-Modified and initialization+6h are availability proxies. For multipart uploads Last-Modified can be upload initiation, not completion; no certified historical publication claim.',
            'multipart_pattern_objects': sum(bool(r.get('etag_multipart_pattern')) for _, r, _ in records)}
        result['inventory_plan_sha256'] = planning.digest(result)
        immutable_json(root/INVENTORY, result)
        progress(root, 'inventory', {k: v for k, v in result.items() if k not in {'ranges', 'objects', 'source_files_sha256'}})
        return result


def read_inventory(root):
    root = Path(root)
    parent = planning.read_plan(root)
    manifest = read_json(root/INVENTORY)
    expected = manifest.pop('inventory_plan_sha256')
    if planning.digest(manifest) != expected or manifest['parent_plan_sha256'] != parent['plan_sha256']:
        raise ValueError('Frozen NOAA byte-range plan changed')
    if manifest['version'] != VERSION or not manifest['complete']:
        raise ValueError('Incomplete or incompatible inventory')
    for path, expected_sha in manifest['source_files_sha256'].items():
        if planning.sha(root/path) != expected_sha:
            raise ValueError('Frozen inventory file changed: '+path)
    manifest['inventory_plan_sha256'] = expected
    return manifest


def validate_range_headers(response, span):
    """Must run before reading one byte, so HTTP200 cannot download the globe."""
    if response.status_code != 206:
        raise ValueError('Expected206 partial content; body not consumed')
    h = requests.structures.CaseInsensitiveDict(response.headers)
    expected = f"bytes {span['start']}-{span['end']}/{span['object_bytes']}"
    if h.get('Content-Range') != expected:
        raise ValueError('Content-Range differs from frozen object/range')
    if h.get('Content-Length') != str(span['bytes']):
        raise ValueError('Content-Length differs from frozen range')
    if h.get('ETag') != span['etag']:
        raise ValueError('Object ETag changed after inventory')
    if h.get('Content-Encoding', 'identity').lower() != 'identity':
        raise ValueError('Compressed HTTP content cannot preserve GRIB byte range')
    modified = header_time(h.get('Last-Modified'))
    if modified.isoformat() != span['last_modified'] or modified >= pd.Timestamp(span['last_modified_must_be_strictly_before_utc']).to_pydatetime():
        raise ValueError('Object modification timestamp changed or is too late')


def validate_grib(raw, span):
    if len(raw) != span['bytes'] or len(raw) < 20 or raw[:4] != b'GRIB' or raw[7] != 2:
        raise ValueError('GRIB magic, edition or range length invalid')
    if int.from_bytes(raw[8:16], 'big') != len(raw) or raw[-4:] != b'7777':
        raise ValueError('GRIB encoded message length or terminator invalid')
    import eccodes
    gid = eccodes.codes_new_from_message(raw)
    keys = ['edition', 'discipline', 'parameterCategory', 'parameterNumber', 'units',
            'typeOfLevel', 'level', 'dataDate', 'dataTime', 'startStep', 'endStep',
            'forecastTime', 'indicatorOfUnitOfTimeRange', 'validityDate', 'validityTime',
            'stepType', 'gridType', 'Ni', 'Nj', 'iDirectionIncrementInDegrees',
            'jDirectionIncrementInDegrees', 'latitudeOfFirstGridPointInDegrees',
            'latitudeOfLastGridPointInDegrees', 'longitudeOfFirstGridPointInDegrees',
            'longitudeOfLastGridPointInDegrees', 'numberOfDataPoints', 'iScansNegatively',
            'jScansPositively', 'jPointsAreConsecutive', 'alternativeRowScanning']
    try:
        meta = {key: eccodes.codes_get(gid, key) for key in keys}
    finally:
        eccodes.codes_release(gid)
    expected_fields = {'temperature': (0, 0, 2, 'K'), 'relative_humidity': (1, 1, 2, '%'),
                       'u_wind': (2, 2, 10, 'm s**-1'), 'v_wind': (2, 3, 10, 'm s**-1')}
    category, parameter, level, units = expected_fields[span['field']]
    init = pd.Timestamp(span['initialization'])
    valid = init + pd.Timedelta(hours=span['lead_hours'])
    expected = {'edition': 2, 'discipline': 0, 'parameterCategory': category,
        'parameterNumber': parameter, 'level': level, 'units': units,
        'typeOfLevel': 'heightAboveGround', 'dataDate': int(init.strftime('%Y%m%d')),
        'dataTime': init.hour*100, 'startStep': span['lead_hours'], 'endStep': span['lead_hours'],
        'forecastTime': span['lead_hours'], 'indicatorOfUnitOfTimeRange': 1,
        'validityDate': int(valid.strftime('%Y%m%d')), 'validityTime': valid.hour*100,
        'stepType': 'instant', 'gridType': 'regular_ll', 'Ni': 1440, 'Nj': 721,
        'iDirectionIncrementInDegrees': .25, 'jDirectionIncrementInDegrees': .25,
        'latitudeOfFirstGridPointInDegrees': 90., 'latitudeOfLastGridPointInDegrees': -90.,
        'longitudeOfFirstGridPointInDegrees': 0., 'longitudeOfLastGridPointInDegrees': 359.75,
        'numberOfDataPoints': 1440*721, 'iScansNegatively': 0, 'jScansPositively': 0,
        'jPointsAreConsecutive': 0, 'alternativeRowScanning': 0}
    for key, value in expected.items():
        if meta[key] != value:
            raise ValueError('GRIB header differs from frozen forecast: '+key)
    meta['eccodes_api_version'] = eccodes.codes_get_api_version()
    return meta


def read_field(root, inventory_sha, span):
    path = root/RAW/'fields'/(span['range_id']+'.json')
    record = read_json(path)
    if record['inventory_plan_sha256'] != inventory_sha or record['range_sha256'] != planning.digest(span):
        raise ValueError('Cached field belongs to a different frozen range')
    binary_path = root/record['binary_path']
    if binary_path.stat().st_size != span['bytes'] or planning.sha(binary_path) != record['sha256']:
        raise ValueError('Cached GRIB field integrity failure')
    return record


def fetch_one(root, inventory_sha, span, client=None, grib_validator=validate_grib):
    root = Path(root)
    metadata_path = root/RAW/'fields'/(span['range_id']+'.json')
    if metadata_path.exists():
        return read_field(root, inventory_sha, span)
    response = (client or session()).get(span['source_url'], timeout=(10, 60), stream=True,
        allow_redirects=False, headers={'Range': f"bytes={span['start']}-{span['end']}",
                                       'If-Match': span['etag'], 'Accept-Encoding': 'identity'})
    try:
        validate_range_headers(response, span)
        raw = limited_body(response, span['bytes'])
        if len(raw) != span['bytes']:
            raise ValueError('Short range response')
        headers = dict(response.headers)
    finally:
        response.close()
    meta = grib_validator(raw, span)
    binary_path = RAW/'fields'/(span['range_id']+'.grib2')
    record = {'version': VERSION, 'inventory_plan_sha256': inventory_sha,
        'range_sha256': planning.digest(span), 'range_id': span['range_id'],
        'observed_at': now(), 'source_url': span['source_url'], 'response_status': 206,
        'response_headers': headers, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'timing_evidence_class': span.get('timing_evidence_class', 's3_availability_proxy_not_certified_publication'),
        'etag_multipart_pattern': span.get('etag_multipart_pattern'),
        'binary_path': str(binary_path), 'grib_metadata': meta,
        'weather_values_classified': False, 'outcomes_used': False}
    immutable_bytes(root/binary_path, raw)
    immutable_json(metadata_path, record)
    return record


def fetch(root, *, workers=4, max_ranges=None):
    root = Path(root)
    with stage_lock(root, 'fetch'):
        if (root/FETCH_MANIFEST).exists():
            return read_fetch_manifest(root)
        manifest = read_inventory(root)
        inventory_sha = manifest['inventory_plan_sha256']
        pending = [r for r in manifest['ranges'] if not (root/RAW/'fields'/(r['range_id']+'.json')).exists()]
        if max_ranges is not None:
            pending = pending[:max_ranges]
        _, failures = _parallel(root, 'fetch', pending, lambda r: fetch_one(root, inventory_sha, r), workers)
        records, missing, integrity_errors = [], [], []
        for span in manifest['ranges']:
            path = root/RAW/'fields'/(span['range_id']+'.json')
            if not path.exists():
                missing.append(span['range_id'])
                continue
            try:
                records.append(read_field(root, inventory_sha, span))
            except Exception as exc:
                integrity_errors.append(_failure(root, 'fetch', span['range_id'], exc))
        status = {'version': VERSION, 'inventory_plan_sha256': inventory_sha, 'observed_at': now(),
            'complete': not missing and not integrity_errors, 'planned_ranges': len(manifest['ranges']),
            'verified_ranges': len(records), 'verified_bytes': sum(r['bytes'] for r in records),
            'missing_ranges': missing, 'failures_this_run': failures+integrity_errors,
            'weather_values_classified': False, 'outcomes_used': False}
        progress(root, 'fetch', status)
        if status['complete']:
            if (root/FETCH_MANIFEST).exists():
                old = read_json(root/FETCH_MANIFEST)
                if old['inventory_plan_sha256'] != inventory_sha:
                    raise ValueError('Existing completed fetch is for a different inventory')
                return old
            source_hashes = {}
            for record in records:
                path = RAW/'fields'/(record['range_id']+'.json')
                source_hashes[str(path)] = planning.sha(root/path)
                source_hashes[record['binary_path']] = record['sha256']
            result = {**status, 'source_files_sha256': source_hashes,
                      'excluded_inventory_objects': manifest['excluded_objects']}
            result['fetch_manifest_sha256'] = planning.digest(result)
            immutable_json(root/FETCH_MANIFEST, result)
            return result
        return status


def read_fetch_manifest(root):
    """Verify the completed byte archive before any extraction or outcome join."""
    root = Path(root)
    inventory_manifest = read_inventory(root)
    manifest = read_json(root/FETCH_MANIFEST)
    expected = manifest.pop('fetch_manifest_sha256')
    if planning.digest(manifest) != expected:
        raise ValueError('Completed NOAA fetch manifest changed')
    if (manifest['inventory_plan_sha256'] != inventory_manifest['inventory_plan_sha256'] or
            manifest['version'] != VERSION or not manifest['complete']):
        raise ValueError('NOAA fetch is incomplete or belongs to another inventory')
    for path, expected_sha in manifest['source_files_sha256'].items():
        if planning.sha(root/path) != expected_sha:
            raise ValueError('Frozen downloaded field changed: '+path)
    manifest['fetch_manifest_sha256'] = expected
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument('--inventory', action='store_true')
    stage.add_argument('--fetch', action='store_true')
    stage.add_argument('--verify-inventory', action='store_true')
    stage.add_argument('--verify-fetch', action='store_true')
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 5))
    parser.add_argument('--max-objects', type=int)
    parser.add_argument('--max-ranges', type=int)
    args = parser.parse_args()
    for limit in (args.max_objects, args.max_ranges):
        if limit is not None and limit <= 0:
            parser.error('Batch limits must be positive')
    if args.inventory:
        result = inventory(args.root, workers=args.workers, max_objects=args.max_objects)
    elif args.fetch:
        result = fetch(args.root, workers=args.workers, max_ranges=args.max_ranges)
    elif args.verify_inventory:
        result = read_inventory(args.root)
    else:
        result = read_fetch_manifest(args.root)
    print(json.dumps({k: v for k, v in result.items() if k not in {'objects', 'ranges', 'source_files_sha256',
          'missing_objects', 'missing_ranges', 'failures_this_run'}}, indent=2, allow_nan=False))
    if not result.get('complete', False):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
