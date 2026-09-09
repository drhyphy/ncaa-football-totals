"""Independently audit frozen NOAA download receipts; never decode values."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def read_manifest(path, digest_key):
    data = json.loads(path.read_text())
    expected = data.pop(digest_key)
    assert digest(data) == expected, 'Manifest digest mismatch: '+path.name
    data[digest_key] = expected
    return data


def audit(root):
    root = Path(root).resolve()
    raw = Path('data/raw/noaa_weather_research')
    plan = read_manifest(root/'reports/noaa_weather_request_plan.json', 'plan_sha256')
    inventory = read_manifest(root/raw/'inventory_plan.json', 'inventory_plan_sha256')
    downloaded = read_manifest(root/raw/'fetch_manifest.json', 'fetch_manifest_sha256')
    assert inventory['parent_plan_sha256'] == plan['plan_sha256']
    assert downloaded['inventory_plan_sha256'] == inventory['inventory_plan_sha256']
    assert inventory['complete'] and downloaded['complete']
    assert not downloaded['missing_ranges'] and not downloaded['failures_this_run']
    assert not inventory['outcomes_used'] and not inventory['weather_values_classified']
    assert not downloaded['outcomes_used'] and not downloaded['weather_values_classified']
    source_file_count = 0
    for manifest in (plan, inventory):
        for path, checksum in manifest['source_files_sha256'].items():
            assert file_sha(root/path) == checksum, 'Frozen upstream file changed: '+path
            source_file_count += 1
    request_map = {row['request_id']: row for row in plan['requests']}
    expected_pairs = {(request['request_id'], field) for request in plan['requests'] for field in request['fields']}
    available_ids = {row['request_id'] for row in inventory['objects'] if row['status']=='available'}
    expected_pairs = {pair for pair in expected_pairs if pair[0] in available_ids}
    actual_pairs = [(span['request_id'], span['field']) for span in inventory['ranges']]
    assert len(actual_pairs) == len(set(actual_pairs)) and set(actual_pairs) == expected_pairs
    expected_files, by_field, receipts, publication_lags, margins = set(), defaultdict(lambda: [0,0]), [], [], []
    multipart, all_bytes = 0, 0
    inventory_time = datetime.fromisoformat(inventory['observed_at'])
    field_definitions = {'temperature': (0,0,2,'K'), 'relative_humidity': (1,1,2,'%'),
                         'u_wind': (2,2,10,'m s**-1'), 'v_wind': (2,3,10,'m s**-1')}
    for span in inventory['ranges']:
        request = request_map[span['request_id']]
        assert span['source_url'] == request['source_url']
        assert span['initialization'] == request['initialization'] and span['lead_hours'] == request['lead_hours']
        assert span['selector'] == plan['field_selectors'][span['field']]
        candidate = {k:v for k,v in span.items() if k != 'range_id'}
        assert digest(candidate)[:32] == span['range_id']
        metadata_path = raw/'fields'/(span['range_id']+'.json')
        binary_path = raw/'fields'/(span['range_id']+'.grib2')
        expected_files.update((str(metadata_path),str(binary_path)))
        receipt = json.loads((root/metadata_path).read_text())
        assert file_sha(root/metadata_path) == downloaded['source_files_sha256'][str(metadata_path)]
        checksum = file_sha(root/binary_path)
        assert checksum == receipt['sha256'] == downloaded['source_files_sha256'][str(binary_path)]
        assert receipt['binary_path'] == str(binary_path)
        assert receipt['inventory_plan_sha256'] == inventory['inventory_plan_sha256']
        assert receipt['range_sha256'] == digest(span) and receipt['range_id'] == span['range_id']
        assert receipt['source_url'] == span['source_url'] and receipt['response_status'] == 206
        assert not receipt['outcomes_used'] and not receipt['weather_values_classified']
        assert receipt['bytes'] == span['bytes'] == span['end']-span['start']+1 == (root/binary_path).stat().st_size
        with (root/binary_path).open('rb') as stream:
            prefix = stream.read(16); stream.seek(-4,2); suffix = stream.read(4)
        assert prefix[:4] == b'GRIB' and prefix[7] == 2 and suffix == b'7777'
        assert int.from_bytes(prefix[8:16],'big') == span['bytes']
        h = {k.lower():v for k,v in receipt['response_headers'].items()}
        assert h['content-range'] == f"bytes {span['start']}-{span['end']}/{span['object_bytes']}"
        assert int(h['content-length']) == span['bytes'] and h['etag'] == span['etag']
        assert h.get('content-encoding','identity') == 'identity'
        modified = parsedate_to_datetime(h['last-modified']).astimezone(timezone.utc)
        initialization = datetime.fromisoformat(span['initialization'])
        deadline = datetime.fromisoformat(request['last_modified_must_be_strictly_before_utc'])
        assert modified.isoformat() == span['last_modified'] and modified < deadline
        assert initialization+timedelta(hours=6) <= deadline
        observed = datetime.fromisoformat(receipt['observed_at'])
        assert observed >= inventory_time
        receipts.append(observed)
        publication_lags.append((modified-initialization).total_seconds()/3600)
        margins.append((deadline-modified).total_seconds()/3600)
        multipart += bool(receipt['etag_multipart_pattern'])
        meta = receipt['grib_metadata']
        category, parameter, level, units = field_definitions[span['field']]
        assert (meta['parameterCategory'],meta['parameterNumber'],meta['level'],meta['units']) == (category,parameter,level,units)
        assert meta['typeOfLevel'] == 'heightAboveGround' and meta['stepType'] == 'instant'
        assert meta['dataDate'] == int(initialization.strftime('%Y%m%d')) and meta['dataTime'] == initialization.hour*100
        assert meta['startStep'] == meta['endStep'] == meta['forecastTime'] == span['lead_hours']
        assert meta['indicatorOfUnitOfTimeRange'] == 1
        valid = initialization+timedelta(hours=span['lead_hours'])
        assert meta['validityDate'] == int(valid.strftime('%Y%m%d')) and meta['validityTime'] == valid.hour*100
        assert meta['Ni'] == 1440 and meta['Nj'] == 721 and meta['numberOfDataPoints'] == 1440*721
        assert all(meta[key] == 0 for key in ['iScansNegatively','jScansPositively','jPointsAreConsecutive','alternativeRowScanning'])
        by_field[span['field']][0] += 1; by_field[span['field']][1] += span['bytes']; all_bytes += span['bytes']
    assert expected_files == set(downloaded['source_files_sha256'])
    assert len(inventory['ranges']) == downloaded['verified_ranges'] == downloaded['planned_ranges']
    assert all_bytes == inventory['exact_field_bytes'] == downloaded['verified_bytes']
    result = {'version':'noaa-download-independent-audit-v1','audited_at':datetime.now(timezone.utc).isoformat(),
        'status':'passed','plan_sha256':plan['plan_sha256'],
        'inventory_plan_sha256':inventory['inventory_plan_sha256'],'fetch_manifest_sha256':downloaded['fetch_manifest_sha256'],
        'upstream_source_files_verified':source_file_count,'download_files_verified':len(expected_files),
        'available_objects':len(available_ids),'excluded_objects':inventory['excluded_objects'],
        'verified_ranges':len(inventory['ranges']),'verified_field_bytes':all_bytes,'inventory_bytes':inventory['index_bytes'],
        'by_field':{key:{'messages':v[0],'bytes':v[1]} for key,v in sorted(by_field.items())},
        'first_receipt_utc':min(receipts).isoformat(),'last_receipt_utc':max(receipts).isoformat(),
        'source_last_modified_after_initialization_hours':[min(publication_lags),max(publication_lags)],
        'source_last_modified_before_decision_hours':[min(margins),max(margins)],
        'multipart_pattern_field_receipts':multipart,'forecast_values_decoded':False,'outcomes_read':False,
        'limitations':['Hashes and HTTP range receipts validate the frozen download, not historical bookmaker execution.',
            'S3 Last-Modified remains an availability proxy; no certified dissemination claim, even without multipart patterns.',
            'Binary framing and recorded header metadata were audited; meteorological value extraction and outcome evaluation were not performed.']}
    directory = root/'reports'
    (directory/'noaa_weather_download_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    lines = ['# Independent NOAA download audit','',
        f"Passed: {result['verified_ranges']:,} GRIB messages from {result['available_objects']:,} forecast objects; {all_bytes:,} field bytes. No exclusions or missing ranges. All {len(expected_files):,} field/receipt files match the completed fetch manifest.",'',
        f"Fetch manifest: `{result['fetch_manifest_sha256']}`",'',
        '| Field | Messages | Bytes |','|---|---:|---:|',
        *[f"| {key} | {v[0]:,} | {v[1]:,} |" for key,v in sorted(by_field.items())],'',
        'Every recorded HTTP response is206 with the exact frozen range, object size, ETag and source Last-Modified. Every binary matches its own receipt and the final manifest SHA-256, declared size, GRIB2 framing/encoded length and terminator. Source/request relationships, valid times, field/level/units and scan-order metadata agree with the frozen plan. No alternate object or later cycle was substituted.','',
        f"Source Last-Modified is {min(publication_lags):.3f}–{max(publication_lags):.3f} hours after initialization and {min(margins):.3f}–{max(margins):.3f} hours before the earliest applicable decision. There are {multipart} multipart-pattern field receipts. Actual new download receipts span {min(receipts).isoformat()} through {max(receipts).isoformat()}.",'',
        'This audit read no weather values, classifications, final scores or betting results. Historical source timestamps remain availability proxies; they do not establish certified dissemination or an executable historical wager. Meteorological extraction and outcome checks remain separate.','',
        'Reproduce from the repository root: `python scripts/audit_noaa_download.py --root model`.']
    (directory/'NOAA_WEATHER_DOWNLOAD_AUDIT.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]/'model')
    args = parser.parse_args()
    print(json.dumps(audit(args.root),indent=2))
