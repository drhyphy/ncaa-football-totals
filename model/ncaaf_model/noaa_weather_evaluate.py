"""Fixed NOAA weather replication: freeze classifications before outcome joins.

No network access, live model changes, threshold search, or executable-price
claims. The extract stage reads forecast/identity inputs only; evaluation is a
separate invocation requiring its immutable, hash-checked classification file.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from .noaa_weather_research import (
    RAW, SEASONS, THRESHOLDS, bilinear_points, digest, forecast_times,
    object_url, read_plan, sha, write_new,
)

VERSION = 'noaa-original-weather-evaluation-v1'
PROTOCOL = Path('reports/NOAA_WEATHER_EVALUATION_PROTOCOL.md')
CLASSIFICATIONS = RAW / 'weather_classifications.json'
EXTRACTION = Path('reports/noaa_weather_extraction.json')
RESULTS = Path('reports/noaa_weather_results.json')
BOOTSTRAP_DRAWS = 10000
BOOTSTRAP_SEED = 20260909
MPH_PER_MPS = 2.2369362920544

# GRIB2 discipline/category/number, level and native unit. Do not infer a
# near-matching surface variable from its numerical values or human label.
FIELD_SPECS = {
    'temperature': (0, 0, 0, 2, 'K'),
    'relative_humidity': (0, 1, 1, 2, '%'),
    'u_wind': (0, 2, 2, 10, 'm s**-1'),
    'v_wind': (0, 2, 3, 10, 'm s**-1'),
}
GRID = {
    'gridType': 'regular_ll', 'Ni': 1440, 'Nj': 721,
    'latitudeOfFirstGridPointInDegrees': 90., 'longitudeOfFirstGridPointInDegrees': 0.,
    'latitudeOfLastGridPointInDegrees': -90., 'longitudeOfLastGridPointInDegrees': 359.75,
    'iDirectionIncrementInDegrees': .25, 'jDirectionIncrementInDegrees': .25,
    'iScansNegatively': 0, 'jScansPositively': 0, 'jPointsAreConsecutive': 0,
    'alternativeRowScanning': 0, 'numberOfDataPoints': 1440 * 721,
}
META_KEYS = tuple(GRID) + (
    'edition', 'discipline', 'parameterCategory', 'parameterNumber',
    'typeOfLevel', 'level', 'units', 'dataDate', 'dataTime', 'forecastTime',
    'stepUnits', 'indicatorOfUnitOfTimeRange', 'stepType', 'validityDate', 'validityTime', 'uvRelativeToGrid',
)


def utc(value):
    """Reject naive, invalid, or numeric dates rather than guessing their units."""
    if not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError('A timezone-aware timestamp is required')
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid timestamp') from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('A timezone-aware timestamp is required')
    return stamp.tz_convert('UTC')


def grib_datetime(date, time):
    if isinstance(date, bool) or isinstance(time, bool):
        raise ValueError('Invalid GRIB date/time')
    try:
        if float(date) != int(date) or float(time) != int(time):
            raise ValueError('Noninteger GRIB date/time')
        return pd.Timestamp(datetime.strptime(f'{int(date):08d}{int(time):04d}', '%Y%m%d%H%M'), tz='UTC')
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid GRIB date/time') from exc


def validate_metadata(metadata, request, field):
    if field not in FIELD_SPECS:
        raise ValueError('Unplanned weather field')
    discipline, category, parameter, level, units = FIELD_SPECS[field]
    expected = {**GRID, 'edition': 2, 'discipline': discipline,
                'parameterCategory': category, 'parameterNumber': parameter,
                'typeOfLevel': 'heightAboveGround', 'level': level,
                'units': units, 'stepUnits': 1, 'indicatorOfUnitOfTimeRange': 1, 'stepType': 'instant'}
    if field in ('u_wind', 'v_wind'):
        expected['uvRelativeToGrid'] = 0
    for key, value in expected.items():
        actual = metadata.get(key)
        if isinstance(value, float):
            valid = isinstance(actual, (int, float)) and math.isfinite(actual) and abs(actual - value) <= 1e-8
        else:
            valid = actual == value
        if not valid:
            raise ValueError('Invalid GRIB metadata: ' + key)
    initialization = utc(request['initialization'])
    lead = int(request['lead_hours'])
    if grib_datetime(metadata.get('dataDate'), metadata.get('dataTime')) != initialization:
        raise ValueError('GRIB initialization differs from frozen request')
    if metadata.get('forecastTime') != lead or lead < 48:
        raise ValueError('GRIB forecast lead differs from frozen request')
    if grib_datetime(metadata.get('validityDate'), metadata.get('validityTime')) != initialization + pd.Timedelta(hours=lead):
        raise ValueError('GRIB valid time differs from frozen request')
    if request['source_url'] != object_url(initialization, lead):
        raise ValueError('Source URL differs from frozen NOAA product')
    for game in request['games']:
        if initialization + pd.Timedelta(hours=6) > utc(game['decision_time']):
            raise ValueError('Initialization publication buffer misses decision')


def grid_indexes(points):
    """Indices for the explicitly validated north-to-south, west-to-east grid."""
    if len(points) != 4:
        raise ValueError('Exactly four frozen bilinear points required')
    weights = np.array([float(p['weight']) for p in points])
    if not np.isfinite(weights).all() or (weights < 0).any() or abs(weights.sum() - 1) > 1e-10:
        raise ValueError('Bilinear weights must be nonnegative and conserve one')
    indexes = []
    for point in points:
        lat, lon = float(point['latitude']), float(point['longitude_0_360'])
        row, column = (90 - lat) * 4, lon * 4
        if not np.isfinite([row, column]).all() or not 0 <= row <= 720 or not 0 <= column <= 1439 or abs(row-round(row)) > 1e-8 or abs(column-round(column)) > 1e-8:
            raise ValueError('Point is outside the fixed quarter-degree grid')
        indexes.append(int(round(row)) * 1440 + int(round(column)))
    return indexes


def bilinear_value(values, points):
    grid_indexes(points)
    values = np.asarray(values, dtype=float)
    if values.shape != (4,) or not np.isfinite(values).all():
        raise ValueError('Four finite field values required')
    return float(np.dot(values, [p['weight'] for p in points]))


def physically_valid(values, field):
    values = np.asarray(values, float)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite forecast values')
    # Broad sanity bounds, not a weather-rule refinement or optimization.
    valid = ((values > 0) & (values < 400)).all() if field == 'temperature' else (
        ((values >= 0) & (values <= 100)).all() if field == 'relative_humidity' else
        (np.abs(values) <= 200).all())
    if not valid:
        raise ValueError('Physically invalid ' + field)


def classify_weather(game, hours):
    """Components are interpolated first; scalar speed is then averaged in time."""
    times = forecast_times(game['kickoff'])
    for key in ('initialization', 'decision_time', 'forecast_leads', 'valid_hours'):
        if game[key] != times[key]:
            raise ValueError('Game times differ from the frozen timing convention')
    expected_points = bilinear_points(game['coordinates']['latitude'], game['coordinates']['longitude'])
    if game['bilinear_points'] != expected_points:
        raise ValueError('Frozen bilinear points differ from venue coordinates')
    if len(hours) != 4 or [x['valid_time'] for x in hours] != times['valid_hours']:
        raise ValueError('All four exact ordered forecast hours required')
    temperature = humidity = None
    speeds = []
    measurements = []
    for h, hour in enumerate(hours):
        values = {}
        required = ['u_wind', 'v_wind'] + (['temperature', 'relative_humidity'] if h == 0 else [])
        for field in required:
            raw = hour['fields'][field]
            physically_valid(raw, field)
            values[field] = bilinear_value(raw, expected_points)
        speed = math.hypot(values['u_wind'], values['v_wind']) * MPH_PER_MPS
        speeds.append(speed)
        measurements.append({'valid_time': hour['valid_time'], 'u_mps': values['u_wind'],
                             'v_mps': values['v_wind'], 'speed_mph': speed})
        if h == 0:
            temperature = (values['temperature'] - 273.15) * 1.8 + 32
            humidity = values['relative_humidity']
    wind = float(np.mean(speeds))
    return {'temperature_f': temperature, 'relative_humidity_percent': humidity,
            'wind_mph': wind, 'hourly_wind': measurements,
            'shadow_under_flag': bool(wind > THRESHOLDS['wind_mph_above'] and
                                     temperature < THRESHOLDS['temperature_f_below'] and
                                     humidity > THRESHOLDS['relative_humidity_percent_above'])}


def settle_under(actual, line):
    actual, line = np.asarray(actual, float), np.asarray(line, float)
    if actual.shape != line.shape or not np.isfinite(actual).all() or not np.isfinite(line).all() or (actual < 0).any() or (actual != np.floor(actual)).any() or ((line < 15) | (line > 100)).any():
        raise ValueError('Finite integer final scores and repaired reference totals required')
    return np.where(actual < line, 100/110, np.where(actual > line, -1., 0.))


def metrics(profits):
    values = np.asarray(profits, float)
    n = len(values)
    wins, losses = int((values > 0).sum()), int((values < 0).sum())
    return {'bets': n, 'wins': wins, 'losses': losses, 'pushes': int((values == 0).sum()),
            'profit_units': float(values.sum()), 'roi': float(values.mean()) if n else None,
            'win_rate_excluding_pushes': wins/(wins+losses) if wins+losses else None}


def active_week_cluster_t(counts, profits):
    n, p = np.asarray(counts, float), np.asarray(profits, float)
    if n.ndim != 1 or n.shape != p.shape or not np.isfinite(n).all() or not np.isfinite(p).all() or (n < 0).any() or (n != np.floor(n)).any() or ((n == 0) & (p != 0)).any():
        raise ValueError('Invalid weekly count/profit vectors')
    active = n > 0
    n, p = n[active], p[active]
    g, total = len(n), float(n.sum())
    theta = float(p.sum()/total) if total else None
    result = {'active_weeks': g, 'roi': theta, 'standard_error': None,
              'interval_95': None, 'interval_99': None,
              'method': 'CR1 ratio-score sandwich, approximate t(active weeks−1); excludes zero-stake weeks'}
    if g < 2:
        return {**result, 'status': 'insufficient_active_weeks'}
    scores = p - theta*n
    se = float(np.sqrt(g/(g-1)*np.dot(scores, scores))/total)
    if se <= 1e-15:
        return {**result, 'status': 'degenerate_cluster_variance'}
    result.update(status='descriptive_only', standard_error=se, df=g-1)
    for label, alpha in (('95', .05), ('99', .01)):
        width = float(student_t.ppf(1-alpha/2, g-1))*se
        result['interval_'+label] = [theta-width, theta+width]
    return result


def weekly_summary(frame, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """Identical sampled weeks for selected and benchmark ratio denominators."""
    if not isinstance(draws, int) or draws <= 0:
        raise ValueError('Positive bootstrap draw count required')
    required = ['game_id', 'kickoff', 'actual_total', 'market_total', 'shadow_under_flag']
    if not set(required).issubset(frame.columns) or frame.game_id.duplicated().any():
        raise ValueError('One complete row per covered game required')
    if not frame.shadow_under_flag.map(lambda x: isinstance(x, (bool, np.bool_))).all():
        raise ValueError('Weather flags must be saved Boolean classifications')
    selected = frame.shadow_under_flag.to_numpy(bool)
    profit = settle_under(frame.actual_total, frame.market_total)
    dates = pd.Series([utc(value).tz_convert('America/New_York') for value in frame.kickoff])
    if len(dates):
        local = dates.dt.tz_localize(None).dt.normalize()
        weeks = (local-pd.to_timedelta(dates.dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
    else:
        weeks = pd.Series([], dtype=str)
    blocks = pd.DataFrame({'week': weeks, 'all_n': 1., 'all_profit': profit,
                           'selected_n': selected.astype(float), 'selected_profit': profit*selected}).groupby('week', sort=True).sum()
    result = {'games': len(frame), 'calendar_week_blocks': len(blocks),
              'weather_rule': metrics(profit[selected]), 'all_under_same_weather_coverage': metrics(profit),
              'nonselected_games': metrics(profit[~selected]), 'price_assumption': -110,
              'bootstrap_draws': draws, 'bootstrap_seed': seed,
              'bootstrap_draws_with_no_rule_bets': draws, 'bootstrap_valid_rule_draws': 0,
              'weather_rule_roi_95_week_bootstrap': None, 'weather_rule_roi_99_week_bootstrap': None,
              'rule_minus_all_under_roi': None, 'rule_minus_all_under_roi_95_paired_week_bootstrap': None,
              'rule_minus_all_under_roi_99_paired_week_bootstrap': None,
              'active_week_cluster_t': active_week_cluster_t(blocks.selected_n, blocks.selected_profit),
              'leave_one_week_out_roi_range': None, 'weeks': []}
    if not len(blocks):
        return result
    array = blocks[['all_n', 'all_profit', 'selected_n', 'selected_profit']].to_numpy()
    rng = np.random.default_rng(seed)
    sums = array[rng.integers(0, len(array), size=(draws, len(array)))].sum(axis=1)
    valid = sums[:, 2] > 0
    result['bootstrap_draws_with_no_rule_bets'] = int((~valid).sum())
    result['bootstrap_valid_rule_draws'] = int(valid.sum())
    if selected.any():
        result['rule_minus_all_under_roi'] = result['weather_rule']['roi']-result['all_under_same_weather_coverage']['roi']
    # A one-week resample has no empirical uncertainty information.
    if valid.any() and len(blocks) >= 2:
        selected_draws = sums[valid, 3]/sums[valid, 2]
        contrast = selected_draws-sums[valid, 1]/sums[valid, 0]
        for label, quantiles in (('95', [.025, .975]), ('99', [.005, .995])):
            result[f'weather_rule_roi_{label}_week_bootstrap'] = [float(x) for x in np.quantile(selected_draws, quantiles)]
            result[f'rule_minus_all_under_roi_{label}_paired_week_bootstrap'] = [float(x) for x in np.quantile(contrast, quantiles)]
    n, p = int(selected.sum()), float(profit[selected].sum())
    leave = []
    for week, row in blocks.iterrows():
        rest_n = n-int(row.selected_n)
        rest_roi = (p-float(row.selected_profit))/rest_n if rest_n else None
        result['weeks'].append({'week': week, 'covered_games': int(row.all_n),
                                'selected_bets': int(row.selected_n), 'selected_profit_units': float(row.selected_profit),
                                'all_under_profit_units': float(row.all_profit),
                                'leave_one_week_out_bets': rest_n, 'leave_one_week_out_roi': rest_roi})
        if rest_roi is not None:
            leave.append(rest_roi)
    if leave:
        result['leave_one_week_out_roi_range'] = [min(leave), max(leave)]
    return result


def validate_receipt(record, span, request, raw):
    """Independently check pinned HTTP byte identity and historical availability."""
    if record.get('range_sha256') != digest(span) or record.get('range_id') != span['range_id'] or record.get('source_url') != request['source_url'] or span['source_url'] != request['source_url']:
        raise ValueError('Field receipt does not match the frozen range/source')
    if span['field'] not in request['fields'] or span['initialization'] != request['initialization'] or span['lead_hours'] != request['lead_hours']:
        raise ValueError('Field range does not match the requested field/time')
    if record.get('response_status') != 206:
        raise ValueError('Field response was not HTTP 206')
    headers = {key.lower(): str(value) for key, value in record.get('response_headers', {}).items()}
    if headers.get('content-range') != f"bytes {span['start']}-{span['end']}/{span['object_bytes']}" or headers.get('content-length') != str(span['bytes']):
        raise ValueError('Field Content-Range/Length differs from frozen range')
    if headers.get('etag') != span['etag'] or headers.get('content-encoding', 'identity').lower() != 'identity':
        raise ValueError('Field HTTP object identity/encoding changed')
    try:
        modified = utc(parsedate_to_datetime(headers.get('last-modified')))
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError('Missing/invalid original object Last-Modified') from exc
    if modified != utc(span['last_modified']) or any(modified >= utc(game['decision_time']) for game in request['games']):
        raise ValueError('Object Last-Modified changed or is not before decision')
    if len(raw) != span['bytes'] or len(raw) != record.get('bytes') or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
        raise ValueError('Pinned field byte size or hash differs')
    if len(raw) < 20 or raw[:4] != b'GRIB' or raw[7] != 2 or int.from_bytes(raw[8:16], 'big') != len(raw) or raw[-4:] != b'7777':
        raise ValueError('Not exactly one complete GRIB2 message')


def decode_points(raw, request, field, game_points):
    """Decode only the frozen grid indices; no nearest-point/weather fallback.

    ecCodes element API: https://sites.ecmwf.int/docs/eccodes/namespaceec_codes.html
    """
    import eccodes
    gid = eccodes.codes_new_from_message(raw)
    try:
        keys = [key for key in META_KEYS if key != 'uvRelativeToGrid' or field in ('u_wind', 'v_wind')]
        metadata = {key: eccodes.codes_get(gid, key) for key in keys}
        validate_metadata(metadata, request, field)
        flat = [index for points in game_points.values() for index in grid_indexes(points)]
        if eccodes.codes_get(gid, 'bitmapPresent'):
            bitmap = np.asarray(eccodes.codes_get_elements(gid, 'bitmap', flat))
        else:
            bitmap = np.ones(len(flat))
        values = np.asarray(eccodes.codes_get_double_elements(gid, 'values', flat), float)
        missing = float(eccodes.codes_get(gid, 'missingValue'))
        rows = {}
        for index, game_id in enumerate(game_points):
            selected = values[4*index:4*index+4]
            bits = bitmap[4*index:4*index+4]
            if not (bits == 1).all() or (selected == missing).any():
                rows[game_id] = {'status': 'unavailable', 'reason': 'required_grid_point_missing'}
                continue
            try:
                physically_valid(selected, field)
            except ValueError as exc:
                rows[game_id] = {'status': 'unavailable', 'reason': str(exc)}
                continue
            rows[game_id] = {'status': 'available', 'values': selected.tolist()}
        return {'metadata': metadata, 'eccodes_version': eccodes.codes_get_api_version(), 'games': rows}
    except eccodes.CodesInternalError as exc:
        raise ValueError('GRIB decoder failure: '+type(exc).__name__) from exc
    finally:
        eccodes.codes_release(gid)


def read_archives(root, plan):
    """No network calls; reject partial fetches and mismatched source plans."""
    from .noaa_weather_fetch import read_inventory, read_fetch_manifest, INVENTORY, FETCH_MANIFEST
    validate_plan_links(plan)
    inventory = read_inventory(root)
    fetched = read_fetch_manifest(root)
    if inventory['parent_plan_sha256'] != plan['plan_sha256'] or fetched['inventory_plan_sha256'] != inventory['inventory_plan_sha256']:
        raise ValueError('Forecast archive belongs to another request plan')
    requests = {r['request_id']: r for r in plan['requests']}
    objects = {r['request_id']: r for r in inventory['objects']}
    if len(requests) != len(plan['requests']) or len(objects) != len(inventory['objects']) or set(objects) != set(requests):
        raise ValueError('Complete one-to-one object inventory required')
    ranges = {}
    seen_ids = set()
    for span in inventory['ranges']:
        key = (span['request_id'], span['field'])
        if key in ranges or span['range_id'] in seen_ids or span['request_id'] not in requests:
            raise ValueError('Duplicate or unplanned archived field range')
        ranges[key] = span
        seen_ids.add(span['range_id'])
    for request_id, request in requests.items():
        obj = objects[request_id]
        actual = {field for rid, field in ranges if rid == request_id}
        if obj['status'] == 'available':
            if actual != set(request['fields']):
                raise ValueError('Available source lacks an exact complete planned field set')
        elif obj['status'] != 'excluded' or actual or not obj.get('reason'):
            raise ValueError('Missing explicit terminal source status')
    if fetched.get('verified_ranges') != len(ranges) or fetched.get('planned_ranges') != len(ranges):
        raise ValueError('Incomplete verified range counts')
    pinned = {str(INVENTORY): sha(root/INVENTORY), str(FETCH_MANIFEST): sha(root/FETCH_MANIFEST)}
    pinned.update(inventory['source_files_sha256'])
    pinned.update(fetched['source_files_sha256'])
    for span in ranges.values():
        for suffix in ('.json', '.grib2'):
            path = str(RAW/'fields'/(span['range_id']+suffix))
            if path not in fetched['source_files_sha256']:
                raise ValueError('Field record/bytes are absent from completed manifest')
    return inventory, fetched, objects, ranges, pinned


def validate_plan_links(plan):
    games = {str(row['game_id']): row for row in plan['games']}
    if len(games) != len(plan['games']):
        raise ValueError('Duplicate planned game')
    expected = {(gid, hour, field) for gid in games for hour in range(4)
                for field in (['u_wind', 'v_wind'] + (['temperature', 'relative_humidity'] if hour == 0 else []))}
    actual = set()
    for request in plan['requests']:
        fields = set()
        if request['source_url'] != object_url(request['initialization'], request['lead_hours']):
            raise ValueError('Frozen request URL differs from its cycle/lead')
        for item in request['games']:
            gid, offset = str(item['game_id']), item['hour_offset']
            if gid not in games or type(offset) is not int or offset not in range(4):
                raise ValueError('Invalid request game/hour linkage')
            game = games[gid]
            required = {'u_wind', 'v_wind'} | ({'temperature', 'relative_humidity'} if offset == 0 else set())
            if request['initialization'] != game['initialization'] or request['lead_hours'] != game['forecast_leads'][offset] or item['decision_time'] != game['decision_time'] or set(item['fields']) != required or len(item['fields']) != len(required):
                raise ValueError('Request game/hour timing or required fields disagree')
            for field in required:
                key = (gid, offset, field)
                if key in actual:
                    raise ValueError('Duplicate request game/hour/field linkage')
                actual.add(key)
            fields.update(required)
        if fields != set(request['fields']) or len(fields) != len(request['fields']):
            raise ValueError('Request union of required fields disagrees')
    if actual != expected:
        raise ValueError('Missing required game/hour/field linkage')


def extraction(root):
    root = Path(root).resolve()
    if (root/CLASSIFICATIONS).exists() or (root/EXTRACTION).exists():
        raise FileExistsError('Weather classifications are immutable; refusing replacement')
    plan = read_plan(root)
    if plan['plan_sha256'] not in (root/PROTOCOL).read_text():
        raise ValueError('Evaluation protocol does not name the frozen request plan')
    inventory, fetched, objects, ranges, pinned = read_archives(root, plan)
    pinned[str(PROTOCOL)] = sha(root/PROTOCOL)
    pinned['ncaaf_model/noaa_weather_evaluate.py'] = sha(Path(__file__))
    games = {str(g['game_id']): g for g in plan['games']}
    if len(games) != len(plan['games']):
        raise ValueError('Duplicate planned game')
    observations = {gid: {} for gid in games}
    failures = {gid: [] for gid in games}
    field_audit = []
    for count, request in enumerate(plan['requests'], 1):
        request_id = request['request_id']
        obj = objects[request_id]
        if obj['status'] == 'excluded':
            for identity in request['games']:
                failures[identity['game_id']].append('source_object_excluded:'+obj['reason'])
            continue
        for field in request['fields']:
            span = ranges[(request_id, field)]
            path = RAW/'fields'/(span['range_id']+'.json')
            record = json.loads((root/path).read_text())
            expected_binary = str(RAW/'fields'/(span['range_id']+'.grib2'))
            if record.get('binary_path') != expected_binary or record.get('inventory_plan_sha256') != inventory['inventory_plan_sha256']:
                raise ValueError('Pinned field path/parent differs')
            raw = (root/expected_binary).read_bytes()
            # Archive identity failures stop the stage instead of becoming a
            # convenient weather-availability exclusion.
            validate_receipt(record, span, request, raw)
            affected = [item for item in request['games'] if field in item['fields']]
            points = {item['game_id']: games[item['game_id']]['bilinear_points'] for item in affected}
            audit_row = {'request_id': request_id, 'field': field, 'range_id': span['range_id'],
                         'source_url': span['source_url'], 'sha256': record['sha256'],
                         'etag': span['etag'], 'etag_multipart_pattern': bool(re.search(r'-\d+"$', span['etag'])),
                         'last_modified': span['last_modified'],
                         'timing_evidence_class': 'original_s3_metadata_availability_proxy_not_certified_publication'}
            try:
                decoded = decode_points(raw, request, field, points)
            except ValueError as exc:
                audit_row.update(status='unavailable', reason=str(exc))
                for item in affected:
                    failures[item['game_id']].append('field_invalid:'+field+':'+str(exc))
                field_audit.append(audit_row)
                continue
            audit_row.update(status='decoded', metadata=decoded['metadata'], eccodes_version=decoded['eccodes_version'])
            field_audit.append(audit_row)
            for item in affected:
                gid, offset = item['game_id'], item['hour_offset']
                result = decoded['games'][gid]
                if result['status'] != 'available':
                    failures[gid].append('grid_value_unavailable:'+field+':'+result['reason'])
                    continue
                hour = observations[gid].setdefault(offset, {'valid_time': games[gid]['valid_hours'][offset], 'fields': {}, 'source_ranges': {}})
                if field in hour['fields']:
                    raise ValueError('Duplicate game/hour/field observation')
                hour['fields'][field] = result['values']
                hour['source_ranges'][field] = span['range_id']
        if count % 100 == 0:
            print(json.dumps({'stage': 'extract', 'objects_checked': count, 'planned_objects': len(plan['requests'])}), flush=True)
    rows = []
    identity_columns = ('game_id', 'season', 'week', 'home_id', 'away_id', 'home_team', 'away_team',
                        'market_source', 'venue_id', 'kickoff', 'decision_time', 'initialization')
    for gid, game in games.items():
        row = {key: game[key] for key in identity_columns}
        row['raw_hourly_grid_values'] = [observations[gid][h] for h in sorted(observations[gid])]
        row['unavailable_reasons'] = sorted(set(failures[gid]))
        if not row['unavailable_reasons']:
            try:
                row.update(classify_weather(game, row['raw_hourly_grid_values']))
            except (ValueError, KeyError) as exc:
                row['unavailable_reasons'].append('incomplete_or_invalid_game_weather:'+str(exc))
        row['status'] = 'unavailable' if row['unavailable_reasons'] else 'available'
        if row['status'] != 'available':
            row['shadow_under_flag'] = None
        rows.append(row)
    output = {'version': VERSION, 'plan_sha256': plan['plan_sha256'],
              'inventory_plan_sha256': inventory['inventory_plan_sha256'],
              'fetch_manifest_sha256': fetched['fetch_manifest_sha256'],
              'classified_at': datetime.now(timezone.utc).isoformat(),
              'outcomes_joined': False, 'thresholds': THRESHOLDS, 'source_files_sha256': pinned,
              'games': rows, 'field_audit': field_audit,
              'timing_evidence_class': 'original_s3_metadata_availability_proxy_not_certified_publication',
              'timing_limitation': 'S3 Last-Modified is not an independently certified publication receipt; multipart objects record upload initiation, which may precede completion. Multipart storage does not change eligibility.',
              'physical_checks': 'All four required grid values finite; 0<K<400; 0<=RH<=100%; |U|,|V|<=200m/s; missing bitmap rejected.'}
    output['classification_sha256'] = digest(output)
    write_new(root/CLASSIFICATIONS, output)
    reasons = Counter(reason for row in rows for reason in row['unavailable_reasons'])
    compact = {'version': VERSION, 'plan_sha256': plan['plan_sha256'], 'classified_at': output['classified_at'],
               'classification_path': str(CLASSIFICATIONS), 'classification_sha256': output['classification_sha256'],
               'classification_file_sha256': sha(root/CLASSIFICATIONS), 'outcomes_joined': False,
               'planned_games': len(rows), 'available_games': sum(row['status']=='available' for row in rows),
               'qualifying_weather_games': sum(row['shadow_under_flag'] is True for row in rows),
               'unavailable_games': sum(row['status']!='available' for row in rows),
               'timing_evidence_class': output['timing_evidence_class'], 'timing_limitation': output['timing_limitation'],
               'multipart_field_ranges': sum(row['etag_multipart_pattern'] for row in field_audit),
               'unavailable_reason_counts': dict(reasons), 'reasons_may_overlap': True}
    write_new(root/EXTRACTION, compact)
    return compact


def read_classifications(root):
    root = Path(root)
    plan = read_plan(root)
    receipt = json.loads((root/EXTRACTION).read_text())
    if receipt['classification_path'] != str(CLASSIFICATIONS) or sha(root/CLASSIFICATIONS) != receipt['classification_file_sha256']:
        raise ValueError('Immutable classification bytes changed')
    classified = json.loads((root/CLASSIFICATIONS).read_text())
    expected = classified.pop('classification_sha256')
    if digest(classified) != expected or expected != receipt['classification_sha256'] or classified['plan_sha256'] != plan['plan_sha256'] or classified['thresholds'] != THRESHOLDS or classified.get('outcomes_joined') is not False:
        raise ValueError('Classification source plan/content changed')
    for path, expected_sha in classified['source_files_sha256'].items():
        if sha(root/path) != expected_sha:
            raise ValueError('Pinned extraction source changed: '+path)
    classified['classification_sha256'] = expected
    return plan, classified


def load_outcomes(root):
    """Only invoked after loading the frozen weather classification artifact."""
    root = Path(root)
    common = ['game_id', 'season', 'home_id', 'away_id', 'market_source',
              'market_total', 'home_score', 'away_score', 'actual_total', 'status']
    repaired = pd.read_parquet(root/'data/raw/alternative/espn_verified_pregame_games.parquet',
        columns=common, filters=[('season', 'in', list(SEASONS)), ('role_verified', '==', True)])
    rich = pd.read_parquet(root/'data/raw/alternative/cfbd_market_games.parquet',
        columns=common, filters=[('season', 'in', list(SEASONS)), ('validated', '==', True)])
    rich = rich.loc[~rich.game_id.isin(repaired.game_id)].copy()
    rich['market_source'] = 'cfbd_'+rich.market_source.astype(str)
    markets = pd.concat([repaired, rich], ignore_index=True)
    markets['game_id'] = markets.game_id.astype(str)
    if markets.game_id.duplicated().any():
        raise ValueError('Repaired market join is not one-to-one')
    schedules = pd.concat([pd.read_parquet(root/f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet',
        columns=['game_id', 'season', 'home_id', 'away_id', 'home_score', 'away_score', 'status']) for year in SEASONS])
    schedules['game_id'] = schedules.game_id.astype(str)
    if schedules.game_id.duplicated().any():
        raise ValueError('Final schedule join is not one-to-one')
    return markets.merge(schedules, on='game_id', how='left', suffixes=('', '_schedule'), validate='one_to_one')


def join_classified_outcomes(plan, classified, outcomes):
    """Identity mismatches and invalid outcomes remain explicit exclusions."""
    planned = {str(row['game_id']): row for row in plan['games']}
    rows = classified['games']
    if len(rows) != len(planned) or {str(row['game_id']) for row in rows} != set(planned):
        raise ValueError('Saved classifications omit or duplicate planned games')
    if outcomes.game_id.duplicated().any():
        raise ValueError('Duplicate outcome game ID')
    lookup = outcomes.set_index('game_id').to_dict('index')
    covered, unavailable = [], []
    for row in rows:
        gid = str(row['game_id'])
        identity = planned[gid]
        for key in ('season', 'home_id', 'away_id', 'market_source', 'kickoff'):
            if row[key] != identity[key]:
                raise ValueError('Saved classification identity changed: '+key)
        reasons = list(row['unavailable_reasons'])
        if row['status'] != 'available':
            reasons.append('weather_unavailable')
        source = lookup.get(gid)
        if source is None:
            reasons.append('repaired_market_missing')
        else:
            for key in ('season', 'home_id', 'away_id'):
                if source.get(key) != identity[key] or source.get(key+'_schedule') != identity[key]:
                    reasons.append('outcome_identity_disagrees:'+key)
            if source['market_source'] != identity['market_source']:
                reasons.append('repaired_market_source_disagrees')
            if source.get('status') != 'STATUS_FINAL' or source.get('status_schedule') != 'STATUS_FINAL':
                reasons.append('outcome_not_final')
            try:
                h, a, total, line = (float(source[key]) for key in ('home_score', 'away_score', 'actual_total', 'market_total'))
                settle_under(np.array([total]), np.array([line]))
                if not np.isfinite([h, a]).all() or h < 0 or a < 0 or h != int(h) or a != int(a) or h+a != total or source.get('home_score_schedule') != h or source.get('away_score_schedule') != a:
                    reasons.append('final_scores_disagree_or_invalid')
            except (TypeError, ValueError):
                reasons.append('final_total_or_line_invalid')
        if reasons:
            unavailable.append({'game_id': gid, 'season': identity['season'], 'reasons': sorted(set(reasons))})
            continue
        if not isinstance(row.get('shadow_under_flag'), bool):
            raise ValueError('Available classification lacks a Boolean fixed-rule flag')
        expected_flag = row['wind_mph'] > THRESHOLDS['wind_mph_above'] and row['temperature_f'] < THRESHOLDS['temperature_f_below'] and row['relative_humidity_percent'] > THRESHOLDS['relative_humidity_percent_above']
        if row['shadow_under_flag'] != expected_flag:
            raise ValueError('Saved classification contradicts unchanged weather thresholds')
        covered.append({**{key: row[key] for key in ('game_id', 'season', 'home_id', 'away_id', 'market_source', 'kickoff', 'shadow_under_flag', 'wind_mph', 'temperature_f', 'relative_humidity_percent')},
                        'actual_total': total, 'market_total': line})
    columns = ['game_id', 'season', 'home_id', 'away_id', 'market_source', 'kickoff', 'shadow_under_flag',
               'wind_mph', 'temperature_f', 'relative_humidity_percent', 'actual_total', 'market_total']
    return pd.DataFrame(covered, columns=columns), unavailable


def evaluate(root):
    root = Path(root).resolve()
    if (root/RESULTS).exists():
        raise FileExistsError('Fixed NOAA evaluation already exists; refusing replacement')
    plan, classified = read_classifications(root)
    frame, unavailable = join_classified_outcomes(plan, classified, load_outcomes(root))
    availability = sum(row['status']=='available' for row in classified['games'])
    reasons = Counter(reason for row in unavailable for reason in row['reasons'])
    output = {'version': VERSION, 'status': 'separate_reused_development_forecast_source_replication',
              'evaluated_at': datetime.now(timezone.utc).isoformat(), 'plan_sha256': plan['plan_sha256'],
              'classification_sha256': classified['classification_sha256'],
              'classification_file_sha256': sha(root/CLASSIFICATIONS), 'thresholds': THRESHOLDS,
              'protocol_sha256': sha(root/PROTOCOL), 'price_assumption': -110,
              'timing_evidence_class': classified['timing_evidence_class'], 'timing_limitation': classified['timing_limitation'],
              'multipart_field_ranges': sum(row['etag_multipart_pattern'] for row in classified['field_audit']),
              'source_files_sha256': {**plan['source_files_sha256'], **classified['source_files_sha256']},
              'coverage': {'repaired_market_games': plan['counts']['market_games'], 'planned_games': len(plan['games']),
                           'planning_excluded_games': len(plan['excluded']), 'planning_exclusion_reasons': plan['counts']['exclusion_reasons'],
                           'weather_available_games': availability, 'weather_unavailable_games': len(plan['games'])-availability,
                           'weather_available_with_final_valid_market_games': len(frame),
                           'weather_available_but_invalid_market_or_outcome': sum(row['game_id'] in {g['game_id'] for g in classified['games'] if g['status']=='available'} for row in unavailable),
                           'selected_games': int(frame.shadow_under_flag.sum()), 'nonselected_games': int((~frame.shadow_under_flag.astype(bool)).sum()),
                           'evaluation_exclusion_reasons': dict(reasons), 'reasons_may_overlap': True},
              'pooled': weekly_summary(frame),
              'by_season': {str(year): weekly_summary(frame.loc[frame.season.eq(year)].reset_index(drop=True)) for year in SEASONS},
              'by_market_source': {str(source): weekly_summary(group.reset_index(drop=True)) for source, group in frame.groupby('market_source', sort=True)},
              'excluded': unavailable, 'planning_excluded': plan['excluded'],
              'no_live_policy_changes': True, 'credible_executable_edge_established': False,
              'limitations': ['All stakes assume one-unit risk at −110, including pushes; historical offered prices and morning entry timing are unverified.',
                  'Weather classifications were saved before this outcome join. Earlier outcomes were reused in other research, so this is not prospective performance or an untouched overall sample.',
                  'Calendar-week bootstrap and active-week cluster-t intervals are descriptive; cross-week dependence and the unknown full earlier search remain outside their uncertainty estimates.',
                  'No combined estimate with 2024–25 Open-Meteo or 2026 archived-price cohorts: forecast grid, cycle, interpolation, era and source differ.',
                  classified['timing_limitation'],
                  'Venue roof state and schedule metadata were retrospectively retrieved. No station, cycle, threshold or stadium substitutions were selected from outcomes.']}
    write_new(root/RESULTS, output)
    frame.to_parquet(root/RAW/'evaluated_games.parquet', index=False)
    def pct(value):
        return 'unavailable' if value is None else f'{value:+.2%}'
    def interval(value):
        return 'unavailable' if value is None else ' to '.join(pct(x) for x in value)
    lines = ['# Fixed original NOAA forecast replication', '',
             'Separate 2021–2023 development replication using original operational GFS fields. Fixed classifications were archived before this evaluation. All returns assume −110; this does not establish executable positive EV.', '',
             '| Period | Covered | Rule W–L–P | Rule bets | Rule ROI | Descriptive 95% interval | Descriptive 99% interval | All-Under ROI |',
             '|---|---:|---|---:|---:|---|---|---:|']
    for name, summary in [('Pooled', output['pooled']), *output['by_season'].items()]:
        m = summary['weather_rule']
        lines.append(f"| {name} | {summary['games']} | {m['wins']}–{m['losses']}–{m['pushes']} | {m['bets']} | {pct(m['roi'])} | {interval(summary['weather_rule_roi_95_week_bootstrap'])} | {interval(summary['weather_rule_roi_99_week_bootstrap'])} | {pct(summary['all_under_same_weather_coverage']['roi'])} |")
    pooled = output['pooled']
    lines.extend(['', 'Selected minus same-coverage all-Under ROI: '+pct(pooled['rule_minus_all_under_roi'])+
                  '; descriptive paired-week 95% interval '+interval(pooled['rule_minus_all_under_roi_95_paired_week_bootstrap'])+
                  ', 99% interval '+interval(pooled['rule_minus_all_under_roi_99_paired_week_bootstrap'])+'.', '',
                  'Active-week cluster-t 95% interval: '+interval(pooled['active_week_cluster_t']['interval_95'])+
                  '; 99% interval: '+interval(pooled['active_week_cluster_t']['interval_99'])+'.',
                  'Leave-one-calendar-week-out ROI range: '+interval(pooled['leave_one_week_out_roi_range'])+'.', '',
                  'The JSON report retains every source group, nonselected benchmark, week, exclusion and valid/omitted bootstrap draw count. No favorable source is selected.', '',
                  '## Coverage', '', '```json', json.dumps(output['coverage'], indent=2), '```', '', '## Limitations', '',
                  *['- '+value for value in output['limitations']], '',
                  'From the repository root, run two separate invocations after complete downloads: `PYTHONPATH=model python -m ncaaf_model.noaa_weather_evaluate --root model --extract`, then replace `--extract` with `--evaluate`. Existing classifications and results are never overwritten.'])
    (root/'reports/noaa_weather_results.md').write_text('\n'.join(lines)+'\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument('--extract', action='store_true')
    stages.add_argument('--evaluate', action='store_true')
    args = parser.parse_args()
    result = extraction(args.root) if args.extract else evaluate(args.root)
    print(json.dumps({key: value for key, value in result.items() if key not in ('games', 'source_files_sha256', 'by_market_source', 'excluded', 'planning_excluded')}, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
