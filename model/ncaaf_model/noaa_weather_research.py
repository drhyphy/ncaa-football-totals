"""Freeze an outcome-free 2021–23 NOAA GFS stadium forecast request plan.

This module does not fetch weather, classify forecasts, or grade bets. Historical
ESPN venue captures may be ingested separately; these are retrospective metadata.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

VERSION = 'noaa-gfs-original-2021-23-plan-v1'
SEASONS = (2021, 2022, 2023)
THRESHOLDS = {'wind_mph_above': 7.78, 'temperature_f_below': 64.81,
              'relative_humidity_percent_above': 56.8}
RAW = Path('data/raw/noaa_weather_research')
PLAN = Path('reports/noaa_weather_request_plan.json')
CATALOG = Path('data/models/weather_venues_v1.json')
BUCKET = 'https://noaa-gfs-bdp-pds.s3.amazonaws.com'
FIELD_SELECTORS = {'temperature': 'TMP:2 m above ground',
                   'relative_humidity': 'RH:2 m above ground',
                   'u_wind': 'UGRD:10 m above ground',
                   'v_wind': 'VGRD:10 m above ground'}
# Measured message lengths in the independently sampled 2021-09-11 00Z f048
# inventory. They are budget estimates, not invented sizes of unfetched files.
ESTIMATED_FIELD_BYTES = {'temperature': 503614, 'relative_humidity': 779487,
                         'u_wind': 961645, 'v_wind': 944663}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def parse_venue_identities(body):
    """Extract only event identity, kickoff, venue, roof and neutral metadata."""
    rows = []
    if not isinstance(body, dict) or not isinstance(body.get('events'), list):
        raise ValueError('Malformed historical ESPN event listing')
    for event in body['events']:
        competitions = event.get('competitions') or []
        if len(competitions) != 1:
            continue
        comp = competitions[0]
        competitors = comp.get('competitors') or []
        home = [x for x in competitors if x.get('homeAway') == 'home']
        away = [x for x in competitors if x.get('homeAway') == 'away']
        if len(home) != 1 or len(away) != 1:
            continue
        venue = comp.get('venue') or {}
        rows.append({'game_id': str(event.get('id', '')),
                     'competition_id': str(comp.get('id', event.get('id', ''))),
                     'kickoff': comp.get('date') or event.get('date'),
                     'home_id': str((home[0].get('team') or {}).get('id', '')),
                     'away_id': str((away[0].get('team') or {}).get('id', '')),
                     'venue_id': str(venue.get('id', '')), 'venue_name': venue.get('fullName'),
                     'indoor': venue.get('indoor'), 'neutral_site': comp.get('neutralSite'),
                     'city': (venue.get('address') or {}).get('city'),
                     'state': (venue.get('address') or {}).get('state')})
    return rows


def ingest_venue_capture(root, season, response_path):
    """Pin previously downloaded public metadata; ingestion is not past receipt."""
    root, response_path = Path(root), Path(response_path)
    if season not in SEASONS:
        raise ValueError('Only fixed 2021–2023 seasons are allowed')
    raw = response_path.read_bytes()
    body = json.loads(raw)
    rows = parse_venue_identities(body)
    # The endpoint silently falls back to 25 rows for unsupported limit=2000.
    # Exactly 1000 may be truncated; neither case is accepted as a season file.
    if not 500 <= len(body['events']) < 1000:
        raise ValueError('Historical season listing is sparse or potentially truncated')
    archive_path = root / RAW / f'espn_venues_{season}.json.gz'
    identity_path = root / RAW / f'espn_venue_identity_{season}.json'
    if archive_path.exists() or identity_path.exists():
        raise FileExistsError('Historical venue capture already pinned')
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open('xb') as stream:
        stream.write(gzip.compress(raw, mtime=0))
    envelope = {'version': VERSION, 'season': season,
        'source_url': f'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates={season}0801-{season+1}0201&groups=80&limit=1000',
        'ingested_at': datetime.now(timezone.utc).isoformat(),
        'timing_evidence': 'Retrieved now; no claim of contemporaneously captured historical roof metadata',
        'raw_path': str(archive_path.relative_to(root)), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'archive_sha256': sha(archive_path), 'event_count': len(body['events']),
        'identities': rows, 'outcome_fields_used': False}
    write_new(identity_path, envelope)
    return {'season': season, 'events': len(rows), 'identity_sha256': sha(identity_path)}


def load_market_identities(root):
    """Same source precedence/range as the repaired model, no score columns."""
    root = Path(root)
    common = ['game_id', 'season', 'week', 'home_id', 'away_id', 'home_team', 'away_team', 'market_source']
    repaired_path = root / 'data/raw/alternative/espn_verified_pregame_games.parquet'
    rich_path = root / 'data/raw/alternative/cfbd_market_games.parquet'
    repaired = pd.read_parquet(repaired_path, columns=common,
        filters=[('season', 'in', list(SEASONS)), ('role_verified', '==', True),
                 ('market_total', '>=', 15), ('market_total', '<=', 100)])
    rich = pd.read_parquet(rich_path, columns=common,
        filters=[('season', 'in', list(SEASONS)), ('validated', '==', True),
                 ('market_total', '>=', 15), ('market_total', '<=', 100)])
    # A rejected repaired source row is not replaced merely for its line size.
    repaired_ids = pd.read_parquet(repaired_path, columns=['game_id'],
        filters=[('season', 'in', list(SEASONS)), ('role_verified', '==', True)]).game_id
    rich = rich.loc[~rich.game_id.isin(repaired_ids)].copy()
    rich['market_source'] = 'cfbd_' + rich.market_source.astype(str)
    games = pd.concat([repaired, rich], ignore_index=True)
    if games.game_id.duplicated().any():
        raise ValueError('Duplicate repaired market identity')
    columns = ['game_id', 'season', 'week', 'game_date', 'home_id', 'away_id', 'neutral_site', 'venue']
    schedules = pd.concat([pd.read_parquet(root / f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet',
                                         columns=columns) for year in SEASONS], ignore_index=True)
    return games.merge(schedules, on=['game_id', 'season', 'week', 'home_id', 'away_id'],
                       how='left', validate='one_to_one').sort_values(['game_date', 'game_id'])


def bilinear_points(latitude, longitude):
    """Fixed 0.25-degree spherical-grid neighbors/weights, no elevation fit."""
    lat, lon = float(latitude), float(longitude)
    if not math.isfinite(lat) or not math.isfinite(lon) or not -89.75 <= lat <= 89.75 or not -180 <= lon <= 180:
        raise ValueError('Invalid or unsupported venue coordinates')
    lon %= 360
    south, west = math.floor(lat*4)/4, math.floor(lon*4)/4
    y, x = (lat-south)*4, (lon-west)*4
    return [{'latitude': sy, 'longitude_0_360': sx % 360, 'weight': weight}
            for sy, sx, weight in [(south, west, (1-y)*(1-x)),
                                   (south, west+.25, (1-y)*x),
                                   (south+.25, west, y*(1-x)),
                                   (south+.25, west+.25, y*x)]]


def forecast_times(kickoff):
    kickoff = pd.to_datetime(kickoff, utc=True, errors='raise')
    if pd.isna(kickoff):
        raise ValueError('Missing kickoff')
    # Construct wall-clock 06:30 explicitly to remain correct on DST changes.
    decision = pd.Timestamp(str(kickoff.tz_convert('America/New_York').date()) + ' 06:30',
                            tz='America/New_York').tz_convert('UTC')
    first_hour = kickoff.floor('h')
    initialization = (first_hour-pd.Timedelta(hours=48)).floor('6h')
    if decision >= kickoff or initialization+pd.Timedelta(hours=6) > decision:
        raise ValueError('Forecast or kickoff cannot satisfy decision availability')
    leads = [int((first_hour+pd.Timedelta(hours=h)-initialization).total_seconds()/3600) for h in range(4)]
    return {'kickoff': kickoff.isoformat(), 'decision_time': decision.isoformat(),
            'initialization': initialization.isoformat(), 'forecast_leads': leads,
            'valid_hours': [(first_hour+pd.Timedelta(hours=h)).isoformat() for h in range(4)]}


def object_url(initialization, lead):
    init = pd.Timestamp(initialization)
    return f'{BUCKET}/gfs.{init:%Y%m%d}/{init:%H}/atmos/gfs.t{init:%H}z.pgrb2.0p25.f{lead:03d}'


def build_plan(root):
    root = Path(root)
    if (root / PLAN).exists():
        raise FileExistsError('The NOAA plan is frozen; refusing replacement')
    from .weather_shadow import THRESHOLDS as existing_thresholds
    if existing_thresholds != THRESHOLDS:
        raise ValueError('Published weather thresholds changed')
    catalog = json.loads((root / CATALOG).read_text())
    if len(catalog['venues']) != 100:
        raise ValueError('Expected the exact frozen 100-venue catalog')
    sources = [CATALOG, Path('data/raw/alternative/espn_verified_pregame_games.parquet'),
               Path('data/raw/alternative/cfbd_market_games.parquet')]
    context = defaultdict(list)
    context_counts = {}
    for season in SEASONS:
        identity_path = RAW / f'espn_venue_identity_{season}.json'
        envelope = json.loads((root / identity_path).read_text())
        raw_path = Path(envelope['raw_path'])
        if sha(root / raw_path) != envelope['archive_sha256']:
            raise ValueError('Historical venue archive changed')
        raw = gzip.decompress((root / raw_path).read_bytes())
        if hashlib.sha256(raw).hexdigest() != envelope['raw_sha256']:
            raise ValueError('Historical venue raw checksum differs')
        if parse_venue_identities(json.loads(raw)) != envelope['identities']:
            raise ValueError('Venue identity extract differs from raw response')
        sources.extend([identity_path, raw_path, Path(f'data/raw/sportsdataverse/cfb_schedule_{season}.parquet')])
        context_counts[str(season)] = envelope['event_count']
        for item in envelope['identities']:
            context[item['game_id']].append({**item, 'source_path': str(identity_path),
                'source_url': envelope['source_url'], 'raw_sha256': envelope['raw_sha256']})
    included, excluded = [], []
    games = load_market_identities(root)
    for game in games.to_dict('records'):
        gid = str(game['game_id'])
        matches = context.get(gid, [])
        reason = None
        if len(matches) != 1:
            reason = 'historical_event_identity_missing_or_ambiguous'
        else:
            m = matches[0]
            if m['home_id'] != str(game['home_id']) or m['away_id'] != str(game['away_id']):
                reason = 'historical_event_team_ids_disagree'
            elif m['neutral_site'] is not False or game['neutral_site'] is not False:
                reason = 'neutral_or_unknown_neutral'
            elif m['indoor'] is not False:
                reason = 'indoor_or_unknown_roof'
            elif m['venue_id'] not in catalog['venues']:
                reason = 'actual_venue_outside_frozen_catalog'
            else:
                schedule_time = pd.to_datetime(game['game_date'], utc=True, errors='coerce')
                event_time = pd.to_datetime(m['kickoff'], utc=True, errors='coerce')
                if pd.isna(schedule_time) or pd.isna(event_time) or abs(schedule_time-event_time) >= pd.Timedelta(hours=1):
                    reason = 'historical_kickoff_missing_or_disagrees'
        if reason:
            excluded.append({'game_id': gid, 'season': int(game['season']), 'reason': reason})
            continue
        try:
            times = forecast_times(game['game_date'])
        except ValueError:
            excluded.append({'game_id': gid, 'season': int(game['season']), 'reason': 'decision_availability_failed'})
            continue
        coordinates = catalog['venues'][m['venue_id']]
        included.append({'game_id': gid, 'season': int(game['season']), 'week': int(game['week']),
            'home_id': int(game['home_id']), 'away_id': int(game['away_id']),
            'home_team': game['home_team'], 'away_team': game['away_team'],
            'market_source': game['market_source'], 'venue_id': m['venue_id'],
            'venue_name': m['venue_name'], 'coordinates': coordinates,
            'bilinear_points': bilinear_points(coordinates['latitude'], coordinates['longitude']),
            'venue_evidence_source': m['source_path'], 'venue_raw_sha256': m['raw_sha256'],
            'indoor': False, 'neutral_site': False,
            'roof_history_status': 'Retrospective ESPN historical-event indoor=false; original historical roof state not independently attested',
            **times})
    grouped = {}
    for game in included:
        for hour, lead in enumerate(game['forecast_leads']):
            key = (game['initialization'], lead)
            if key not in grouped:
                url = object_url(*key)
                grouped[key] = {'initialization': key[0], 'lead_hours': lead, 'source_url': url,
                    'index_url': url+'.idx', 'fields': set(), 'games': {}}
            entry = grouped[key]
            fields = ['u_wind', 'v_wind'] + (['temperature', 'relative_humidity'] if hour == 0 else [])
            entry['fields'].update(fields)
            entry['games'][game['game_id']] = {'game_id': game['game_id'], 'hour_offset': hour,
                'fields': fields, 'decision_time': game['decision_time']}
    requests = []
    for key, entry in sorted(grouped.items()):
        entry['fields'] = sorted(entry['fields'])
        entry['games'] = sorted(entry['games'].values(), key=lambda x: x['game_id'])
        entry['last_modified_must_be_strictly_before_utc'] = min(g['decision_time'] for g in entry['games'])
        entry['estimated_field_bytes'] = sum(ESTIMATED_FIELD_BYTES[f] for f in entry['fields'])
        entry['request_id'] = digest({'url': entry['source_url'], 'fields': entry['fields']})[:24]
        requests.append(entry)
    code_path = root / RAW / 'frozen_plan_source.py'
    code_path.parent.mkdir(parents=True, exist_ok=True)
    with code_path.open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    sources.append(code_path.relative_to(root))
    plan = {'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'seasons': list(SEASONS), 'thresholds': THRESHOLDS, 'source_kind': 'NOAA original operational GFS0.25 forecast messages',
        'source_files_sha256': {str(p): sha(root/p) for p in sources},
        'field_selectors': FIELD_SELECTORS, 'historical_venue_events': context_counts,
        'timing_policy': 'Latest six-hour initialization <= kickoff UTC floor hour minus48h; same initialization for all four hours; initialization+6h <= gameday06:30America/New_York; S3 Last-Modified strictly before every affected decision',
        'spatial_policy': 'Fixed bilinear0.25degree grid interpolation of TMP/RH/U/V separately; no altitude correction, nearest-airport substitution or station tuning',
        'wind_policy': 'Interpolate U/V separately at venue, then sqrt(U²+V²)*2.2369362920544 at each hour; arithmetic mean of kickoff-floor hour and next three hours',
        'temperature_policy': 'Kickoff-floor hour; kelvin converted to Fahrenheit via (K-273.15)*1.8+32',
        'humidity_policy': 'Kickoff-floor hour2m relative humidity in percent; no dewpoint-derived substitution',
        'download_policy': 'Only planned fields via inventory-derived complete GRIB byte ranges; require206 and exact Content-Range, GRIB init/lead/field/unit checks; pin URL,index,response headers,Last-Modified,byte ranges and SHA256 before extracting weather flags or joining returns',
        'missingness_policy': 'Missing/late source timestamps, invalid fields, unavailable exact cycles, inconsistent grids or missing any required value exclude with reason; no weather-source or later-cycle fallback; partial fetch is never a final evaluation',
        'market_policy': 'Previously repaired provider source precedence; legacy CFBD consensus/selected bookmaker fills missing repaired years; prices and exact market quote time unavailable',
        'outcomes_used_in_this_plan': False, 'forecast_values_used_in_this_plan': False,
        'counts': {'market_games': len(games), 'included_games': len(included),
            'venues': len({g['venue_id'] for g in included}), 'forecast_objects': len(requests),
            'field_range_requests': sum(len(r['fields']) for r in requests),
            'by_season': {str(y): sum(g['season']==y for g in included) for y in SEASONS},
            'exclusion_reasons': dict(Counter(x['reason'] for x in excluded))},
        'budget': {'service_charge_usd': 0, 'estimated_field_bytes': sum(r['estimated_field_bytes'] for r in requests),
            'estimated_inventory_bytes': 42000*len(requests), 'estimation_sample': '2021-09-11 00Z f048 original inventory message lengths; actual sizes unknown until index retrieval',
            'estimated_bytes_per_field': ESTIMATED_FIELD_BYTES, 'forecast_bytes_downloaded_for_this_plan': 0},
        'limitations': ['Forecast product, spatial interpolation and same-initialization four-hour window differ from the existing Open-Meteo experiment; thresholds unchanged.',
            'Historical venue IDs are actual event-specific ESPN metadata retrieved now. Original historical roof status and retrospective schedule corrections are not independently attested.',
            'Coordinates are the unchanged100-venue catalog; no new venues or favorable geography added.',
            'Earlier game outcomes have been used by other model experiments. This is a new frozen forecast-source replication, not prospective publication or untouched overall data.',
            'The source listing contains score fields, but only explicit identity/venue fields are extracted. Repaired market availability and earlier source validation were established before this plan.'],
        'games': included, 'excluded': excluded, 'requests': requests}
    plan['plan_sha256'] = digest(plan)
    write_new(root / PLAN, plan)
    lines = ['# Frozen NOAA2021–2023 weather request plan', '',
        'No forecast classification or return evaluation has occurred. This stage pins the earlier market universe, actual historical-event venue metadata,100-venue coordinate catalog and exact forecast requests.', '',
        f"Plan SHA-256: `{plan['plan_sha256']}`", '',
        f"Included {len(included):,} of {len(games):,} market games at {plan['counts']['venues']} venues; {len(requests):,} shared forecast objects and {plan['counts']['field_range_requests']:,} field ranges.",
        f"Estimated transfer: {(plan['budget']['estimated_field_bytes']+plan['budget']['estimated_inventory_bytes'])/1e9:.3f} GB including indexes; $0 public service fee. Actual index sizes will determine the final byte budget before any field download.", '',
        '| Season | Included games |', '|---|---:|',
        *[f"| {y} | {plan['counts']['by_season'][str(y)]} |" for y in SEASONS], '',
        'Exclusions: '+json.dumps(plan['counts']['exclusion_reasons'], sort_keys=True), '',
        'The same initialization supplies all four wind hours. U/V components are interpolated before scalar speed, then the four speeds are averaged. Thresholds remain wind>7.78mph,temperature<64.81°F,RH>56.8%.', '',
        '## Required before field download or evaluation', '',
        'Retrieve only the locked inventories and headers; freeze exact byte ranges and sizes. Require S3 Last-Modified strictly before every affected06:30Eastern decision and initialization+6h no later than decision. Original data fields, indexes, headers and response hashes must be archived before weather classification or outcome joins.', '',
        *['- '+x for x in plan['limitations']], '',
        'Reproduce after ingesting the documented historical venue response files: `PYTHONPATH=model python -m ncaaf_model.noaa_weather_research --root model --plan`. Existing plans are never overwritten.']
    (root / 'reports/NOAA_WEATHER_REQUEST_PLAN.md').write_text('\n'.join(lines)+'\n')
    return plan


def read_plan(root):
    root = Path(root)
    plan = json.loads((root / PLAN).read_text())
    expected = plan.pop('plan_sha256')
    if digest(plan) != expected or plan['version'] != VERSION or plan['thresholds'] != THRESHOLDS:
        raise ValueError('Frozen NOAA request plan changed')
    for path, expected_sha in plan['source_files_sha256'].items():
        if sha(root/path) != expected_sha:
            raise ValueError('Frozen input changed: '+path)
    plan['plan_sha256'] = expected
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument('--ingest-venue', nargs=2, metavar=('SEASON', 'RESPONSE_FILE'))
    stages.add_argument('--plan', action='store_true')
    stages.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.ingest_venue:
        result = ingest_venue_capture(args.root, int(args.ingest_venue[0]), Path(args.ingest_venue[1]))
    else:
        plan = build_plan(args.root) if args.plan else read_plan(args.root)
        result = {'plan_sha256': plan['plan_sha256'], 'counts': plan['counts'], 'budget': plan['budget']}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
