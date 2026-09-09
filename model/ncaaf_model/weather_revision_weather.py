"""Request specifications and strict, descriptive forecast parsing only.

No weather-rule classification, betting fields, outcomes, fitting or network I/O.
The collector owns HTTP receipt archival and immutable response/cache selection.
Single Runs needs forecast_days=8; absolute-hour bounds are rejected by the API.
Requested run + endpoint contract + first valid hour is source-vintage evidence,
not independent certification of initialization or original publication time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import PurePosixPath


VERSION = 'weather-revision-weather-v1'
SINGLE_URL = 'https://single-runs-api.open-meteo.com/v1/forecast'
PREVIOUS_URL = 'https://previous-runs-api.open-meteo.com/v1/forecast'
MODEL = 'gfs_global'
VARIABLES = ('temperature_2m','relative_humidity_2m','wind_speed_10m')
FORECAST_DAYS = 8
FORECAST_HOURS = FORECAST_DAYS*24
MAX_GRID_DISTANCE_KM = 50.
# Broad ingestion sanity checks; these are not conditions for a strategy.
BOUNDS = {'temperature_2m':(-150.,160.),'relative_humidity_2m':(0.,100.),
          'wind_speed_10m':(0.,250.),'elevation':(-500.,6000.)}


def _time(value):
    try:
        result = value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError,TypeError) as exc:
        raise ValueError('Invalid timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return result.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).isoformat().replace('+00:00','Z')


def _finite(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Finite numeric forecast value required')
    return float(value)


def _bounded(value,name):
    value = _finite(value)
    low,high = BOUNDS[name]
    if not low <= value <= high:
        raise ValueError('Physical sanity bound exceeded: '+name)
    return value


def _venue(venue):
    if not isinstance(venue,dict) or venue.get('venue_id') is None or not str(venue.get('venue_id','')).strip():
        raise ValueError('Explicit venue identity required')
    latitude,longitude = _finite(venue.get('latitude')),_finite(venue.get('longitude'))
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('Invalid requested coordinates')
    return {'venue_id':str(venue['venue_id']),'latitude':latitude,'longitude':longitude}


def select_run(capture_start):
    """Latest six-hour UTC cycle at least six hours before collection starts."""
    value = _time(capture_start)-timedelta(hours=6)
    return value.replace(hour=(value.hour//6)*6,minute=0,second=0,microsecond=0)


def maturity_time(kickoff):
    """Original receipt and request must follow maturity of the last day2 hour."""
    return _time(kickoff).replace(minute=0,second=0,microsecond=0)+timedelta(hours=3-48+6)


def _request_key(spec):
    identity = {key:spec[key] for key in ('product','source_url','parameters','venue','context_id')}
    if spec['product'] == 'previous_day2':
        identity['kickoff'] = spec['kickoff']
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _parameters(venue):
    return {'latitude':venue['latitude'],'longitude':venue['longitude'],'models':MODEL,
        'temperature_unit':'fahrenheit','wind_speed_unit':'mph','timezone':'GMT'}


def single_run_request(venue,capture_start):
    venue = _venue(venue)
    run = select_run(capture_start)
    spec = {'product':'single_run','source_url':SINGLE_URL,
        'parameters':{**_parameters(venue),'hourly':','.join(VARIABLES),
                      'run':run.strftime('%Y-%m-%dT%H:%M'),'forecast_days':FORECAST_DAYS},
        'venue':venue,'context_id':None,'capture_started_at':_stamp(capture_start),
        'requested_run':_stamp(run)}
    spec['request_key'] = _request_key(spec)
    return spec


def previous_day2_request(venue,kickoff,capture_start,context_id):
    """Return None before maturity; the collector then performs no request."""
    venue = _venue(venue)
    kickoff,capture_start = _time(kickoff),_time(capture_start)
    if not str(context_id or '').strip():
        raise ValueError('Immutable game context identity required')
    if capture_start >= kickoff:
        raise ValueError('Pregame collection required')
    if capture_start < maturity_time(kickoff):
        return None
    params = {**_parameters(venue),'hourly':','.join(name+'_previous_day2' for name in VARIABLES),
        'start_date':kickoff.date().isoformat(),'end_date':(kickoff+timedelta(hours=3)).date().isoformat()}
    spec = {'product':'previous_day2','source_url':PREVIOUS_URL,'parameters':params,
        'venue':venue,'context_id':str(context_id),'capture_started_at':_stamp(capture_start),
        'requested_run':None,'kickoff':_stamp(kickoff),'mature_at':_stamp(maturity_time(kickoff))}
    spec['request_key'] = _request_key(spec)
    return spec


def _validate_request(spec,product):
    if not isinstance(spec,dict) or spec.get('product') != product:
        raise ValueError('Forecast product mismatch')
    required = {'venue','capture_started_at','source_url','parameters','context_id','requested_run','request_key'}
    if product == 'previous_day2':
        required |= {'kickoff','mature_at'}
    if required-set(spec):
        raise ValueError('Incomplete prespecified forecast request')
    if product == 'single_run':
        expected = single_run_request(spec['venue'],spec['capture_started_at'])
    else:
        expected = previous_day2_request(spec['venue'],spec['kickoff'],spec['capture_started_at'],spec['context_id'])
    if expected is None or any(spec.get(k) != expected[k] for k in expected):
        raise ValueError('Prespecified forecast request mismatch')


def _receipt(spec,receipt,kickoff):
    """Validate the original HTTP receipt, never manufacture receipt timestamps."""
    if (not isinstance(receipt,dict) or receipt.get('schema_version') != 'raw-http-receipt-v1'
            or receipt.get('status_code') != 200 or receipt.get('transport_error') is not None):
        raise ValueError('Successful original HTTP receipt required')
    requested,received = _time(receipt.get('requested_at')),_time(receipt.get('received_at'))
    if requested > received or received >= kickoff:
        raise ValueError('Receipt ordering is invalid or response arrived after kickoff')
    request = receipt.get('request')
    if not isinstance(request,dict) or request.get('url') != spec['source_url']:
        raise ValueError('Receipt endpoint mismatch')
    if request.get('params') != spec['parameters']:
        raise ValueError('Receipt request parameters mismatch')
    digest = receipt.get('body_sha256','')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Original response body hash required')
    for key in ('body_path','receipt_path'):
        path = receipt.get(key)
        if not isinstance(path,str) or not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
            raise ValueError('Relative original archive paths required')
    return requested,received


def _location(payload,venue):
    lat,lon = _finite(payload.get('latitude')),_finite(payload.get('longitude'))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Invalid returned coordinates')
    a,b = math.radians(venue['latitude']),math.radians(lat)
    dl = math.radians(lon-venue['longitude'])
    hav = math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2
    distance = 2*6371.0088*math.asin(math.sqrt(min(1.,max(0.,hav))))
    if distance > MAX_GRID_DISTANCE_KM:
        raise ValueError('Returned forecast point too far from requested venue')
    return {'latitude':lat,'longitude':lon,'elevation_m':_bounded(payload.get('elevation'),'elevation'),
        'distance_from_requested_km':distance,
        'spatial_processing':'Provider default land-cell/elevation processing; no alternate location requested.'}


def _hourly(payload,suffix):
    if not isinstance(payload,dict) or payload.get('error'):
        raise ValueError('Forecast response must be a successful object')
    if isinstance(payload.get('utc_offset_seconds'),bool) or payload.get('utc_offset_seconds') != 0:
        raise ValueError('UTC forecast response required')
    if payload.get('timezone') not in {'GMT','UTC','Etc/UTC'} or payload.get('timezone_abbreviation') not in {'GMT','UTC'}:
        raise ValueError('UTC forecast timezone metadata required')
    hourly,units = payload.get('hourly'),payload.get('hourly_units')
    if not isinstance(hourly,dict) or not isinstance(units,dict) or units.get('time') != 'iso8601':
        raise ValueError('Hourly forecast schema or time units invalid')
    expected_units = {'temperature_2m':{'°F'},'relative_humidity_2m':{'%'},'wind_speed_10m':{'mp/h','mph'}}
    times = hourly.get('time')
    if not isinstance(times,list) or not times:
        raise ValueError('Forecast time array required')
    parsed = []
    for value in times:
        try:
            # The provider's offset=0 makes these explicitly UTC despite naive strings.
            moment = datetime.strptime(value,'%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        except (TypeError,ValueError) as exc:
            raise ValueError('Invalid forecast valid-time format') from exc
        if moment.strftime('%Y-%m-%dT%H:%M') != value or moment.minute or moment.second or (parsed and moment != parsed[-1]+timedelta(hours=1)):
            raise ValueError('Forecast valid hours must be unique and consecutive')
        parsed.append(moment)
    for name,allowed in expected_units.items():
        values = hourly.get(name+suffix)
        if units.get(name+suffix) not in allowed or not isinstance(values,list) or len(values)!=len(parsed):
            raise ValueError('Weather units or array length invalid: '+name)
    return hourly,parsed


def _parse(payload,spec,receipt,kickoff,product):
    _validate_request(spec,product)
    kickoff = _time(kickoff)
    requested,received = _receipt(spec,receipt,kickoff)
    suffix = '_previous_day2' if product == 'previous_day2' else ''
    hourly,times = _hourly(payload,suffix)
    point = _location(payload,spec['venue'])
    run = _time(spec['requested_run']) if product == 'single_run' else None
    if run is not None:
        if len(times)!=FORECAST_HOURS or times[0]!=run or requested<run+timedelta(hours=6):
            raise ValueError('Single-run origin, full horizon or publication buffer mismatch')
        if requested < _time(spec['capture_started_at']):
            raise ValueError('Single-run request predates this capture; cross-capture reuse prohibited')
    else:
        if kickoff != _time(spec['kickoff']):
            raise ValueError('Fixed-lead context kickoff mismatch')
        if requested < maturity_time(kickoff) or received < maturity_time(kickoff):
            raise ValueError('Original day2 request/receipt predates product maturity')
    required = [kickoff.replace(minute=0,second=0,microsecond=0)+timedelta(hours=i) for i in range(4)]
    lookup = {moment:i for i,moment in enumerate(times)}
    if any(moment not in lookup for moment in required):
        raise ValueError('Required four game hours unavailable')
    values = {name:[_bounded(hourly[name+suffix][lookup[moment]],name)
                    for moment in (required if name == 'wind_speed_10m' else required[:1])]
              for name in VARIABLES}
    # Only kickoff temperature/RH and four winds are required measurements;
    # unused later temperature/RH remain in the original archived response.
    leads = [(moment-run).total_seconds()/3600 for moment in required] if run is not None else None
    result = {'parser_version':VERSION,'product':product,'model':MODEL,
        'request_key':spec['request_key'],'context_id':spec['context_id'],
        'venue':spec['venue'].copy(),'returned_point':point,'kickoff':_stamp(kickoff),
        'requested_run':_stamp(run) if run is not None else None,
        'requested_at':_stamp(requested),'received_at':_stamp(received),
        'source_url':spec['source_url'],'body_sha256':receipt['body_sha256'],
        'body_path':receipt['body_path'],'receipt_path':receipt['receipt_path'],
        'valid_hours_utc':[_stamp(t) for t in required],
        'temperature_f':values['temperature_2m'][0],
        'relative_humidity_percent':values['relative_humidity_2m'][0],
        'wind_mph_by_hour':values['wind_speed_10m'],
        'wind_mph_four_hour_mean':sum(values['wind_speed_10m'])/4,
        'actual_run_lead_hours':leads,
        'native_three_hour_interpolation_by_hour':[lead>120 for lead in leads] if leads is not None else [False]*4,
        'api_hourly_resolution_seconds':3600,
        'initialization_independently_verified':False,'publication_time_independently_verified':False,
        'vintage_evidence':('Requested UTC run + Single Runs endpoint contract + matching first valid hour; body does not independently certify initialization.'
            if run is not None else 'Fixed previous_day2 lead-time series; no single initialization is asserted.'),
        'receipt_evidence':'Actually received provider response; original public dissemination time unverified.'}
    if run is None:
        result['nominal_lead_hours'] = 48
        result['nominal_reference_times_utc'] = [_stamp(t-timedelta(hours=48)) for t in required]
        result['mature_at'] = _stamp(maturity_time(kickoff))
    return result


def parse_single_run(payload,request,receipt,kickoff):
    return _parse(payload,request,receipt,kickoff,'single_run')


def parse_previous_day2(payload,request,receipt,kickoff):
    return _parse(payload,request,receipt,kickoff,'previous_day2')
