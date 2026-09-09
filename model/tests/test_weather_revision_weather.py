"""Offline source, timing and parser checks; no forecast or outcome requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from ncaaf_model import weather_revision_weather as weather


CAPTURE = datetime(2026,9,9,2,tzinfo=timezone.utc)
KICKOFF = datetime(2026,9,12,23,30,tzinfo=timezone.utc)
VENUE = {'venue_id':'3793','latitude':41.65838893680983,'longitude':-91.55147552490234}


def body(start,count,suffix=''):
    return {'latitude':41.66,'longitude':-91.55,'elevation':220.,
        'utc_offset_seconds':0,'timezone':'GMT','timezone_abbreviation':'GMT',
        'hourly_units':{'time':'iso8601','temperature_2m'+suffix:'°F',
            'relative_humidity_2m'+suffix:'%','wind_speed_10m'+suffix:'mp/h'},
        'hourly':{'time':[(start+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(count)],
            'temperature_2m'+suffix:[60.]*count,'relative_humidity_2m'+suffix:[70.]*count,
            'wind_speed_10m'+suffix:[10.]*count}}


def receipt(spec,payload,requested=CAPTURE,received=None):
    return {'schema_version':'raw-http-receipt-v1','requested_at':requested.isoformat(),
        'received_at':(received or requested+timedelta(seconds=1)).isoformat(),
        'status_code':200,'body_sha256':hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        'body_path':'data/runtime/revisions/body.json.gz','receipt_path':'data/runtime/revisions/receipt.json',
        'request':{'url':spec['source_url'],'params':deepcopy(spec['parameters'])},
        'response_headers':{'date':'Wed, 09 Sep 2026 02:00:01 GMT','content_type':'application/json'},
        'transport_error':None}


def single(kickoff=KICKOFF):
    spec = weather.single_run_request(VENUE,CAPTURE)
    payload = body(weather.select_run(CAPTURE),192)
    return payload,spec,receipt(spec,payload),kickoff


def previous():
    capture = weather.maturity_time(KICKOFF)
    spec = weather.previous_day2_request(VENUE,KICKOFF,capture,'context-1')
    start = KICKOFF.replace(hour=0,minute=0,second=0,microsecond=0)
    payload = body(start,48,'_previous_day2')
    return payload,spec,receipt(spec,payload,capture),KICKOFF


def test_cycle_selection_is_utc_and_never_less_than_six_hours_old():
    assert weather.select_run(CAPTURE) == datetime(2026,9,8,18,tzinfo=timezone.utc)
    assert weather.select_run('2026-09-08T22:00:00-04:00') == weather.select_run(CAPTURE)
    for hour in range(24):
        now = CAPTURE.replace(hour=hour,minute=59,second=59)
        run = weather.select_run(now)
        assert run.hour in {0,6,12,18}
        assert timedelta(hours=6) <= now-run < timedelta(hours=12)
    with pytest.raises(ValueError,match='Timezone-aware'):
        weather.select_run('2026-09-09T02:00:00')


def test_single_request_uses_verified_full_run_parameters_and_stable_cache_identity():
    original = deepcopy(VENUE)
    spec = weather.single_run_request(VENUE,CAPTURE)
    assert spec['parameters']['forecast_days'] == 8
    assert spec['parameters']['run'] == '2026-09-08T18:00'
    assert not {'start_hour','end_hour','start_date','end_date'} & set(spec['parameters'])
    assert weather.single_run_request(VENUE,CAPTURE+timedelta(minutes=15))['request_key'] == spec['request_key']
    assert weather.single_run_request(VENUE,CAPTURE+timedelta(hours=6))['request_key'] != spec['request_key']
    assert weather.single_run_request({**VENUE,'venue_id':'3800'},CAPTURE)['request_key'] != spec['request_key']
    assert VENUE == original


def test_single_extracts_exact_four_hours_and_descriptive_values_only():
    payload,spec,raw,kickoff = single()
    start = payload['hourly']['time'].index('2026-09-12T23:00')
    payload['hourly']['wind_speed_10m'][start:start+4] = [12.,6.,18.,0.]
    result = weather.parse_single_run(payload,spec,raw,kickoff)
    assert result['temperature_f'] == 60
    assert result['relative_humidity_percent'] == 70
    assert result['wind_mph_by_hour'] == [12.,6.,18.,0.]
    assert result['wind_mph_four_hour_mean'] == 9
    assert result['valid_hours_utc'] == ['2026-09-12T23:00:00Z','2026-09-13T00:00:00Z',
                                        '2026-09-13T01:00:00Z','2026-09-13T02:00:00Z']
    assert result['actual_run_lead_hours'] == [101.,102.,103.,104.]
    assert result['body_sha256'] == raw['body_sha256']
    assert result['body_path'] == raw['body_path']
    assert result['receipt_path'] == raw['receipt_path']
    assert not result['initialization_independently_verified']
    assert not result['publication_time_independently_verified']
    assert not {'flags','thresholds','weather_rule_match','eligible','bet_eligible','expected_value',
                'win_probability','actual_total','side','model_predictions'} & set(result)


def test_native_resolution_metadata_marks_only_hours_beyond_120():
    result = weather.parse_single_run(*single(datetime(2026,9,13,18,tzinfo=timezone.utc)))
    assert result['actual_run_lead_hours'] == [120.,121.,122.,123.]
    assert result['native_three_hour_interpolation_by_hour'] == [False,True,True,True]


def test_full_seven_day_capture_cohort_and_extra_wind_hours_fit_eight_day_run():
    kickoff = CAPTURE+timedelta(days=7)
    result = weather.parse_single_run(*single(kickoff))
    assert result['valid_hours_utc'][-1] == '2026-09-16T05:00:00Z'
    assert max(result['actual_run_lead_hours']) < 192


@pytest.mark.parametrize('mutation,match',[
    ('missing_array','array length'),('short_array','array length'),('duplicate_time','unique and consecutive'),
    ('unordered_time','unique and consecutive'),('nonhour_time','unique and consecutive'),
    ('short_horizon','full horizon'),('wrong_origin','origin'),('wrong_units','units'),
    ('wrong_timezone','timezone'),('nonzero_offset','UTC'),('boolean_offset','UTC'),
    ('provider_error','successful object'),('nonobject','successful object'),
    ('far_location','too far'),('missing_elevation','Finite'),('bad_elevation','sanity'),
])
def test_corrupt_response_schema_source_or_location_fails_closed(mutation,match):
    payload,spec,raw,kickoff = single()
    if mutation == 'missing_array': del payload['hourly']['wind_speed_10m']
    elif mutation == 'short_array': payload['hourly']['wind_speed_10m'].pop()
    elif mutation == 'duplicate_time': payload['hourly']['time'][2] = payload['hourly']['time'][1]
    elif mutation == 'unordered_time': payload['hourly']['time'].reverse()
    elif mutation == 'nonhour_time': payload['hourly']['time'][0] = '2026-09-08T18:30'
    elif mutation == 'short_horizon':
        for name in payload['hourly']: payload['hourly'][name] = payload['hourly'][name][:168]
    elif mutation == 'wrong_origin': payload = body(weather.select_run(CAPTURE)+timedelta(hours=1),192)
    elif mutation == 'wrong_units': payload['hourly_units']['wind_speed_10m'] = 'km/h'
    elif mutation == 'wrong_timezone': payload['timezone'] = 'America/New_York'
    elif mutation == 'nonzero_offset': payload['utc_offset_seconds'] = 3600
    elif mutation == 'boolean_offset': payload['utc_offset_seconds'] = False
    elif mutation == 'provider_error': payload['error'] = True
    elif mutation == 'nonobject': payload = []
    elif mutation == 'far_location': payload['latitude'] = 44.
    elif mutation == 'missing_elevation': del payload['elevation']
    elif mutation == 'bad_elevation': payload['elevation'] = 10000.
    with pytest.raises(ValueError,match=match):
        weather.parse_single_run(payload,spec,raw,kickoff)


@pytest.mark.parametrize('name,value',[
    ('temperature_2m',-151.),('temperature_2m',161.),('temperature_2m',None),
    ('relative_humidity_2m',-1.),('relative_humidity_2m',101.),
    ('wind_speed_10m',-1.),('wind_speed_10m',251.),('wind_speed_10m',float('nan')),
    ('wind_speed_10m',float('inf')),('wind_speed_10m','10'),('wind_speed_10m',True),
])
def test_required_values_must_be_finite_numeric_and_within_integrity_bounds(name,value):
    payload,spec,raw,kickoff = single()
    i = payload['hourly']['time'].index('2026-09-12T23:00')
    payload['hourly'][name][i] = value
    with pytest.raises(ValueError):
        weather.parse_single_run(payload,spec,raw,kickoff)


@pytest.mark.parametrize('mutation,match',[
    ('status','HTTP'),('transport','HTTP'),('missing_hash','body hash'),('absolute_path','Relative'),
    ('traversal_path','Relative'),('wrong_endpoint','endpoint'),('wrong_params','parameters'),
    ('late_receipt','after kickoff'),('receipt_order','ordering'),('early_request','buffer'),
    ('naive_receipt','Timezone-aware'),
])
def test_original_http_receipt_is_required_and_validated(mutation,match):
    payload,spec,raw,kickoff = single()
    if mutation == 'status': raw['status_code'] = 500
    elif mutation == 'transport': raw['transport_error'] = 'Timeout'
    elif mutation == 'missing_hash': raw['body_sha256'] = None
    elif mutation == 'absolute_path': raw['body_path'] = '/outside/body'
    elif mutation == 'traversal_path': raw['receipt_path'] = 'data/../outside'
    elif mutation == 'wrong_endpoint': raw['request']['url'] = weather.PREVIOUS_URL
    elif mutation == 'wrong_params': raw['request']['params']['run'] = '2026-09-09T00:00'
    elif mutation == 'late_receipt': raw['received_at'] = kickoff.isoformat()
    elif mutation == 'receipt_order': raw['requested_at'] = (CAPTURE+timedelta(hours=1)).isoformat()
    elif mutation == 'early_request': raw['requested_at'] = '2026-09-08T23:59:00Z'
    elif mutation == 'naive_receipt': raw['received_at'] = '2026-09-09T02:00:01'
    with pytest.raises(ValueError,match=match):
        weather.parse_single_run(payload,spec,raw,kickoff)


@pytest.mark.parametrize('mutation',['model','horizon','absolute_range','location','request_key'])
def test_request_spec_cannot_silently_drift(mutation):
    payload,spec,raw,kickoff = single()
    if mutation == 'model': spec['parameters']['models'] = 'best_match'
    elif mutation == 'horizon': spec['parameters']['forecast_days'] = 7
    elif mutation == 'absolute_range': spec['parameters']['start_hour'] = '2026-09-12T23:00'
    elif mutation == 'location': spec['parameters']['latitude'] = 0.
    else: spec['request_key'] = '0'*64
    with pytest.raises(ValueError,match='request mismatch'):
        weather.parse_single_run(payload,spec,raw,kickoff)


def test_previous_day2_request_is_absent_before_maturity_and_cache_key_is_per_context():
    mature = weather.maturity_time(KICKOFF)
    assert mature == datetime(2026,9,11,8,tzinfo=timezone.utc)
    assert weather.previous_day2_request(VENUE,KICKOFF,mature-timedelta(microseconds=1),'ctx') is None
    first = weather.previous_day2_request(VENUE,KICKOFF,mature,'ctx')
    later = weather.previous_day2_request(VENUE,KICKOFF,mature+timedelta(hours=6),'ctx')
    changed = weather.previous_day2_request(VENUE,KICKOFF,mature,'other-ctx')
    assert first['request_key'] == later['request_key'] != changed['request_key']
    assert first['parameters']['start_date'] == '2026-09-12'
    assert first['parameters']['end_date'] == '2026-09-13'
    assert 'run' not in first['parameters']
    assert all(name.endswith('_previous_day2') for name in first['parameters']['hourly'].split(','))


def test_previous_day2_preserves_nominal_leads_without_asserting_one_initialization():
    result = weather.parse_previous_day2(*previous())
    assert result['requested_run'] is None
    assert result['actual_run_lead_hours'] is None
    assert result['nominal_lead_hours'] == 48
    assert result['nominal_reference_times_utc'] == ['2026-09-10T23:00:00Z','2026-09-11T00:00:00Z',
                                                  '2026-09-11T01:00:00Z','2026-09-11T02:00:00Z']
    assert result['wind_mph_four_hour_mean'] == 10


def test_early_day2_cache_cannot_become_mature_as_collection_clock_advances():
    payload,spec,raw,kickoff = previous()
    raw['requested_at'] = (weather.maturity_time(kickoff)-timedelta(hours=1)).isoformat()
    raw['received_at'] = (weather.maturity_time(kickoff)-timedelta(minutes=59)).isoformat()
    spec = weather.previous_day2_request(VENUE,kickoff,weather.maturity_time(kickoff)+timedelta(hours=3),'context-1')
    with pytest.raises(ValueError,match='predates product maturity'):
        weather.parse_previous_day2(payload,spec,raw,kickoff)


def test_changed_day2_kickoff_does_not_reuse_old_context():
    payload,spec,raw,kickoff = previous()
    with pytest.raises(ValueError,match='kickoff mismatch'):
        weather.parse_previous_day2(payload,spec,raw,kickoff+timedelta(minutes=30))


def test_later_same_cycle_capture_cannot_reuse_an_earlier_capture_receipt():
    payload,spec,raw,kickoff = single()
    later = weather.single_run_request(VENUE,CAPTURE+timedelta(minutes=30))
    with pytest.raises(ValueError,match='cross-capture reuse prohibited'):
        weather.parse_single_run(payload,later,raw,kickoff)


def test_mature_day2_context_can_reuse_original_receipt_without_redating_it():
    payload,spec,raw,kickoff = previous()
    later = weather.previous_day2_request(VENUE,kickoff,weather.maturity_time(kickoff)+timedelta(hours=3),'context-1')
    result = weather.parse_previous_day2(payload,later,raw,kickoff)
    assert result['received_at'] == '2026-09-11T08:00:01Z'


def test_unused_later_temperature_humidity_missingness_does_not_remove_valid_game():
    payload,spec,raw,kickoff = single()
    start = payload['hourly']['time'].index('2026-09-12T23:00')
    for name in ('temperature_2m','relative_humidity_2m'):
        payload['hourly'][name][start+1:start+4] = [None]*3
    result = weather.parse_single_run(payload,spec,raw,kickoff)
    assert result['temperature_f'] == 60.
    assert result['relative_humidity_percent'] == 70.
    payload['hourly']['wind_speed_10m'][start+3] = None
    with pytest.raises(ValueError,match='Finite'):
        weather.parse_single_run(payload,spec,raw,kickoff)


def test_incomplete_request_spec_fails_explicitly():
    payload,spec,raw,kickoff = single()
    del spec['capture_started_at']
    with pytest.raises(ValueError,match='Incomplete prespecified'):
        weather.parse_single_run(payload,spec,raw,kickoff)


def test_missing_required_game_hours_never_triggers_a_source_or_cycle_fallback():
    payload,spec,raw,_ = single()
    with pytest.raises(ValueError,match='four game hours unavailable'):
        weather.parse_single_run(payload,spec,raw,'2026-09-17T00:00:00Z')


def test_request_and_parser_inputs_remain_unchanged():
    args = single()
    originals = deepcopy(args)
    weather.parse_single_run(*args)
    assert args == originals
