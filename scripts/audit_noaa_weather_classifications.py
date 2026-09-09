#!/usr/bin/env python3
"""Reconstruct frozen NOAA weather from raw GRIB; never load outcomes.

This script imports no project planning, fetching or evaluation module. Run only
after the root researcher has created the immutable classification artifact.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import eccodes
import numpy as np

PLAN_SHA = '47747f5a0216b5b0a43e9df5777575bfafb165ad6616332869eda35c2b508258'
INVENTORY_SHA = 'f486e78a6a2a4699f3b7c54ae4f9186beab87ed04dcbd8aa234959767a734649'
CLASSIFICATION_SHA = '132874fe46c150121fad2b40d7aefed8b17fb5593f6318b3db3f062ea31d8951'
THRESHOLDS = {'wind_mph_above': 7.78, 'temperature_f_below': 64.81,
              'relative_humidity_percent_above': 56.8}
RAW = Path('data/raw/noaa_weather_research')
FIELDS = {'temperature': (0, 0, 2, 'K'), 'relative_humidity': (1, 1, 2, '%'),
          'u_wind': (2, 2, 10, 'm s**-1'), 'v_wind': (2, 3, 10, 'm s**-1')}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def read_hashed(path, key, expected=None):
    value = json.loads(Path(path).read_text())
    claimed = value.pop(key)
    assert digest(value) == claimed
    if expected is not None:
        assert claimed == expected
    value[key] = claimed
    return value


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert result.tzinfo is not None
    return result.astimezone(timezone.utc)


def required(hour):
    return ['u_wind', 'v_wind'] + (['temperature', 'relative_humidity'] if hour == 0 else [])


def corners(game):
    """Independently construct NW, NE, SW, SE array coordinates."""
    lat = game['coordinates']['latitude']
    lon = game['coordinates']['longitude'] % 360
    south, west = math.floor(lat * 4) / 4, math.floor(lon * 4) / 4
    north, east = south + .25, (west + .25) % 360
    cells = [(int(round((90-y)*4)), int(round(x*4)))
             for y, x in [(north, west), (north, east), (south, west), (south, east)]]
    assert all(0 <= y <= 720 and 0 <= x <= 1439 for y, x in cells)
    return cells, (lat-south)*4, (lon-west)*4


def interpolate(values, north_weight, east_weight):
    nw, ne, sw, se = (float(v) for v in values)
    north = nw + east_weight*(ne-nw)
    south = sw + east_weight*(se-sw)
    return south + north_weight*(north-south)


def grid_metadata(gid, span):
    init = timestamp(span['initialization'])
    valid = init + timedelta(hours=span['lead_hours'])
    category, parameter, level, units = FIELDS[span['field']]
    expected = {'edition': 2, 'discipline': 0, 'parameterCategory': category,
        'parameterNumber': parameter, 'typeOfLevel': 'heightAboveGround', 'level': level,
        'units': units, 'dataDate': int(init.strftime('%Y%m%d')), 'dataTime': init.hour*100,
        'forecastTime': span['lead_hours'], 'indicatorOfUnitOfTimeRange': 1, 'stepUnits': 1,
        'stepType': 'instant', 'startStep': span['lead_hours'], 'endStep': span['lead_hours'],
        'validityDate': int(valid.strftime('%Y%m%d')), 'validityTime': valid.hour*100,
        'gridType': 'regular_ll', 'Ni': 1440, 'Nj': 721, 'numberOfDataPoints': 1440*721,
        'latitudeOfFirstGridPointInDegrees': 90., 'latitudeOfLastGridPointInDegrees': -90.,
        'longitudeOfFirstGridPointInDegrees': 0., 'longitudeOfLastGridPointInDegrees': 359.75,
        'iDirectionIncrementInDegrees': .25, 'jDirectionIncrementInDegrees': .25,
        'iScansNegatively': 0, 'jScansPositively': 0, 'jPointsAreConsecutive': 0,
        'alternativeRowScanning': 0}
    if span['field'] in ('u_wind', 'v_wind'):
        expected['uvRelativeToGrid'] = 0
    for key, value in expected.items():
        if eccodes.codes_get(gid, key) != value:
            raise ValueError('Unexpected metadata: '+key)


def physical(values, field):
    if not np.isfinite(values).all():
        return False
    if field == 'temperature':
        return bool(((values > 0) & (values < 400)).all())
    if field == 'relative_humidity':
        return bool(((values >= 0) & (values <= 100)).all())
    return bool((np.abs(values) <= 200).all())


def audit(root):
    root = Path(root)
    classification_path = root/RAW/'weather_classifications.json'
    if not classification_path.exists():
        raise FileNotFoundError('Wait for the immutable classification before running this audit')
    plan = read_hashed(root/'reports/noaa_weather_request_plan.json', 'plan_sha256', PLAN_SHA)
    inventory = read_hashed(root/RAW/'inventory_plan.json', 'inventory_plan_sha256', INVENTORY_SHA)
    fetched = read_hashed(root/RAW/'fetch_manifest.json', 'fetch_manifest_sha256')
    saved = read_hashed(classification_path, 'classification_sha256', CLASSIFICATION_SHA)
    assert inventory['parent_plan_sha256'] == PLAN_SHA
    assert fetched['inventory_plan_sha256'] == saved['inventory_plan_sha256'] == INVENTORY_SHA
    assert fetched['fetch_manifest_sha256'] == saved['fetch_manifest_sha256']
    assert saved['plan_sha256'] == PLAN_SHA and saved['thresholds'] == plan['thresholds'] == THRESHOLDS
    assert saved['outcomes_joined'] is False and inventory['complete'] and fetched['complete']
    for path, expected in {**plan['source_files_sha256'], **saved['source_files_sha256']}.items():
        assert sha(root/path) == expected, path
    receipt = json.loads((root/'reports/noaa_weather_extraction.json').read_text())
    assert receipt['classification_file_sha256'] == sha(classification_path)
    assert receipt['classification_sha256'] == saved['classification_sha256']
    games = {g['game_id']: g for g in plan['games']}
    rows = {g['game_id']: g for g in saved['games']}
    assert len(games) == len(rows) == len(saved['games']) == 1747 and set(rows) == set(games)
    requests = {r['request_id']: r for r in plan['requests']}
    assert len(requests) == 1275
    observations, failures = {}, defaultdict(list)
    span_counts = Counter()
    raw_grid_error = 0.
    for obj in inventory['objects']:
        if obj['status'] != 'available':
            for g in requests[obj['request_id']]['games']:
                failures[g['game_id']].append('source_object_excluded')
    for count, span in enumerate(inventory['ranges'], 1):
        request = requests[span['request_id']]
        record_path = RAW/'fields'/(span['range_id']+'.json')
        record = json.loads((root/record_path).read_text())
        binary_path = RAW/'fields'/(span['range_id']+'.grib2')
        assert record['binary_path'] == str(binary_path)
        assert record['range_sha256'] == digest(span) and record['response_status'] == 206
        assert record['inventory_plan_sha256'] == INVENTORY_SHA
        raw = (root/binary_path).read_bytes()
        assert len(raw) == span['bytes'] == record['bytes']
        assert hashlib.sha256(raw).hexdigest() == record['sha256'] == fetched['source_files_sha256'][str(binary_path)]
        assert sha(root/record_path) == fetched['source_files_sha256'][str(record_path)]
        headers = {k.lower(): str(v) for k, v in record['response_headers'].items()}
        assert headers['content-range'] == f"bytes {span['start']}-{span['end']}/{span['object_bytes']}"
        assert headers['content-length'] == str(len(raw)) and headers['etag'] == span['etag']
        assert headers.get('content-encoding', 'identity') == 'identity'
        modified = parsedate_to_datetime(headers['last-modified'])
        assert modified == timestamp(span['last_modified'])
        assert all(modified < timestamp(g['decision_time']) for g in request['games'])
        assert raw[:4] == b'GRIB' and raw[7] == 2 and raw[-4:] == b'7777'
        assert int.from_bytes(raw[8:16], 'big') == len(raw)
        affected = [g for g in request['games'] if span['field'] in g['fields']]
        gid = eccodes.codes_new_from_message(raw)
        try:
            try:
                grid_metadata(gid, span)
            except ValueError as exc:
                for g in affected:
                    failures[g['game_id']].append(str(exc))
                continue
            # Decode the full two-dimensional grid, independently of the study's
            # sparse element extraction; use rectangular interpolation algebra.
            values = np.asarray(eccodes.codes_get_values(gid)).reshape(721, 1440)
            bitmap = (np.asarray(eccodes.codes_get_array(gid, 'bitmap')).reshape(721, 1440)
                      if eccodes.codes_get(gid, 'bitmapPresent') else None)
            missing = float(eccodes.codes_get(gid, 'missingValue'))
            for item in affected:
                game, hour, field = games[item['game_id']], item['hour_offset'], span['field']
                cells, wy, wx = corners(game)
                corner_values = np.array([values[y, x] for y, x in cells])
                if ((bitmap is not None and any(bitmap[y, x] != 1 for y, x in cells)) or
                        (corner_values == missing).any() or not physical(corner_values, field)):
                    failures[game['game_id']].append('required_point_missing_or_invalid:'+field)
                    continue
                key = (game['game_id'], hour, field)
                assert key not in observations
                observations[key] = interpolate(corner_values, wy, wx)
                # The study's retained corner vector is south-west first.
                saved_hour = [h for h in rows[game['game_id']]['raw_hourly_grid_values']
                              if h['valid_time'] == game['valid_hours'][hour]]
                assert len(saved_hour) == 1 and saved_hour[0]['source_ranges'][field] == span['range_id']
                raw_saved = np.array(saved_hour[0]['fields'][field])
                err = float(np.max(abs(corner_values[[2, 3, 0, 1]]-raw_saved)))
                raw_grid_error = max(raw_grid_error, err)
                assert err < 1e-10
        finally:
            eccodes.codes_release(gid)
        span_counts[span['field']] += 1
        if count % 250 == 0:
            print(json.dumps({'stage': 'independent_noaa_audit', 'ranges_checked': count,
                              'total_ranges': len(inventory['ranges'])}), flush=True)
    errors = Counter()
    availability, flags, by_season = Counter(), Counter(), defaultdict(Counter)
    independently_computed = []
    for gid, game in games.items():
        row = rows[gid]
        kickoff = timestamp(game['kickoff'])
        first = kickoff.replace(minute=0, second=0, microsecond=0)
        earlier = first-timedelta(hours=48)
        init = earlier.replace(hour=6*(earlier.hour//6))
        decision = datetime.combine(kickoff.astimezone(ZoneInfo('America/New_York')).date(),
                                    datetime.min.time(), ZoneInfo('America/New_York'))+timedelta(hours=6, minutes=30)
        assert init == timestamp(game['initialization']) and decision == timestamp(game['decision_time'])
        assert init+timedelta(hours=6) <= decision < kickoff
        for h in range(4):
            assert timestamp(game['valid_hours'][h]) == first+timedelta(hours=h)
            assert game['forecast_leads'][h] == (first+timedelta(hours=h)-init).total_seconds()/3600
        available = not failures[gid] and all((gid, h, f) in observations for h in range(4) for f in required(h))
        assert (row['status'] == 'available') == available
        availability['available' if available else 'unavailable'] += 1
        if not available:
            assert row['shadow_under_flag'] is None
            independently_computed.append({'game_id': gid, 'status': 'unavailable'})
            continue
        wind = math.fsum(math.sqrt(observations[(gid, h, 'u_wind')]**2+observations[(gid, h, 'v_wind')]**2)
                         *2.2369362920544 for h in range(4))/4
        temperature = (observations[(gid, 0, 'temperature')]-273.15)*9/5+32
        humidity = observations[(gid, 0, 'relative_humidity')]
        flag = wind > 7.78 and temperature < 64.81 and humidity > 56.8
        values = {'wind_mph': wind, 'temperature_f': temperature, 'relative_humidity_percent': humidity}
        for field, value in values.items():
            error = abs(value-row[field])
            errors[field] = max(errors[field], error)
            assert error < 1e-9
        assert row['shadow_under_flag'] is flag
        flags[str(flag)] += 1
        by_season[str(game['season'])]['available'] += 1
        by_season[str(game['season'])]['rule_matches'] += int(flag)
        independently_computed.append({'game_id': gid, 'status': 'available',
                                      'shadow_under_flag': flag, **values})
    result = {'status': 'passed', 'audited_at': datetime.now(timezone.utc).isoformat(),
        'plan_sha256': PLAN_SHA, 'inventory_plan_sha256': INVENTORY_SHA,
        'fetch_manifest_sha256': fetched['fetch_manifest_sha256'],
        'classification_sha256': saved['classification_sha256'],
        'implementation': 'No project module imports. Full GRIB arrays, independent corner ordering and rectangular bilinear algebra; separate UTC/Eastern timing and strict fixed-rule computations.',
        'field_ranges': len(inventory['ranges']), 'field_counts': dict(span_counts),
        'game_field_values': len(observations), 'games': len(games), 'availability': dict(availability),
        'rule_flags': dict(flags), 'by_season': dict(by_season),
        'maximum_raw_grid_error': raw_grid_error, 'maximum_feature_errors': dict(errors),
        'outcomes_read': False, 'return_evaluation_performed': False, 'active_policy_modified': False}
    (root/'reports/noaa_weather_classification_audit.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    (root/RAW/'independent_classification_audit_values.json').write_text(json.dumps(independently_computed, indent=2, allow_nan=False)+'\n')
    print(json.dumps(result, indent=2, allow_nan=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1]/'model')
    audit(parser.parse_args().root)
