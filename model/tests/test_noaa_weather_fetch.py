from copy import deepcopy
import hashlib
import json

import numpy as np
import pytest
import requests

from ncaaf_model import noaa_weather_fetch as nf


def request_row():
    return {'request_id': 'object', 'source_url': 'https://example.test/file',
        'index_url': 'https://example.test/file.idx', 'initialization': '2021-09-11T00:00:00+00:00',
        'lead_hours': 48, 'fields': ['temperature', 'relative_humidity', 'u_wind', 'v_wind'],
        'last_modified_must_be_strictly_before_utc': '2021-09-13T10:30:00+00:00',
        'games': [{'game_id': '1', 'decision_time': '2021-09-13T10:30:00+00:00'}]}


def inventory_bytes():
    return ('1:0:d=2021091100:TMP:2 m above ground:48 hour fcst:\n'
            '2:60:d=2021091100:RH:2 m above ground:48 hour fcst:\n'
            '3:120:d=2021091100:UGRD:10 m above ground:48 hour fcst:\n'
            '4:180:d=2021091100:VGRD:10 m above ground:48 hour fcst:\n').encode()


def headers(size=240):
    return {'Content-Length': str(size), 'ETag': '"original"',
            'Last-Modified': 'Sat, 11 Sep 2021 03:50:31 GMT'}


class Response:
    def __init__(self, status=200, body=b'', response_headers=None):
        self.status_code, self.body = status, body
        self.headers = response_headers or {}
        self.consumed = False
        self.closed = False
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))
    def iter_content(self, chunk_size):
        self.consumed = True
        yield self.body
    def close(self):
        self.closed = True


class Client:
    def __init__(self, head=None, get=None):
        self.head_response, self.get_response = head, get
        self.get_calls = []
    def head(self, *args, **kwargs):
        assert self.head_response is not None
        return self.head_response
    def get(self, *args, **kwargs):
        assert self.get_response is not None
        self.get_calls.append((args, kwargs))
        return self.get_response


def span():
    return {'range_id': 'range', 'request_id': 'object', 'source_url': 'https://example.test/file',
        'field': 'temperature', 'start': 0, 'end': 59, 'bytes': 60, 'object_bytes': 240,
        'etag': '"original"', 'last_modified': '2021-09-11T03:50:31+00:00',
        'initialization': '2021-09-11T00:00:00+00:00', 'lead_hours': 48,
        'last_modified_must_be_strictly_before_utc': '2021-09-13T10:30:00+00:00'}


def range_response(**changes):
    h = {**headers(60), 'Content-Range': 'bytes 0-59/240'}
    h.update(changes)
    return Response(206, b'x'*60, h)


def test_inventory_exact_fields_and_last_message_end():
    ranges = nf.parse_inventory(inventory_bytes(), request_row(), 240)
    assert [r['start'] for r in ranges] == [0, 60, 120, 180]
    assert ranges[-1]['end'] == 239
    assert sum(r['bytes'] for r in ranges) == 240


@pytest.mark.parametrize('mutation', ['duplicate', 'wrong_level', 'wrong_cycle', 'wrong_lead', 'bad_offsets'])
def test_inventory_corruption_never_selects_neighbor_field(mutation):
    data = inventory_bytes()
    if mutation == 'duplicate': data += b'5:240:d=2021091100:TMP:2 m above ground:48 hour fcst:\n'
    elif mutation == 'wrong_level': data = data.replace(b'TMP:2 m above ground', b'TMP:2 mb')
    elif mutation == 'wrong_cycle': data = data.replace(b'd=2021091100', b'd=2021091112')
    elif mutation == 'wrong_lead': data = data.replace(b'48 hour fcst', b'49 hour fcst')
    elif mutation == 'bad_offsets': data = data.replace(b'2:60:', b'2:0:')
    with pytest.raises(nf.SourceExcluded):
        nf.parse_inventory(data, request_row(), 300)


def test_head_cannot_be_available_at_or_after_decision():
    h = headers()
    h['Last-Modified'] = 'Mon, 13 Sep 2021 10:30:00 GMT'
    with pytest.raises(nf.SourceExcluded):
        nf.validate_head(h, request_row()['last_modified_must_be_strictly_before_utc'])
    h.pop('Last-Modified')
    with pytest.raises(nf.SourceExcluded):
        nf.validate_head(h, request_row()['last_modified_must_be_strictly_before_utc'])


def test_inventory_pins_headers_and_index_then_resumes_without_network(tmp_path):
    client = Client(Response(200, response_headers=headers()), Response(200, inventory_bytes()))
    first = nf.inventory_one(tmp_path, 'parent', request_row(), client)
    assert first['status'] == 'available' and first['etag'] == '"original"'
    assert first['index_sha256'] == hashlib.sha256(inventory_bytes()).hexdigest()
    assert nf.inventory_one(tmp_path, 'parent', request_row(), Client()) == first
    (tmp_path/first['index_path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='inventory bytes changed'):
        nf.inventory_one(tmp_path, 'parent', request_row(), Client())


def test_late_head_is_explicit_exclusion_and_does_not_request_index(tmp_path):
    h = headers(); h['Last-Modified'] = 'Wed, 15 Sep 2021 00:00:00 GMT'
    client = Client(Response(200, response_headers=h))
    result = nf.inventory_one(tmp_path, 'parent', request_row(), client)
    assert result['status'] == 'excluded' and result['ranges'] == []
    assert client.get_calls == []


@pytest.mark.parametrize('status', [200, 302, 404, 412, 500])
def test_non206_body_is_closed_without_consuming_full_object(tmp_path, status):
    response = Response(status, b'a potentially huge object', headers(541020405))
    with pytest.raises(ValueError, match='body not consumed'):
        nf.fetch_one(tmp_path, 'inventory', span(), Client(get=response))
    assert response.closed and not response.consumed


@pytest.mark.parametrize('changes', [
    {'Content-Range': 'bytes 1-60/240'}, {'Content-Range': 'bytes 0-59/999'},
    {'Content-Length': '61'}, {'ETag': '"replacement"'},
    {'Last-Modified': 'Sun, 12 Sep 2021 03:50:31 GMT'}, {'Content-Encoding': 'gzip'}])
def test_wrong_range_or_version_rejected_before_body(tmp_path, changes):
    response = range_response(**changes)
    with pytest.raises(ValueError):
        nf.fetch_one(tmp_path, 'inventory', span(), Client(get=response))
    assert response.closed and not response.consumed


def test_field_download_sends_preconditions_and_resume_checks_integrity(tmp_path):
    response = range_response()
    client = Client(get=response)
    first = nf.fetch_one(tmp_path, 'inventory', span(), client, grib_validator=lambda raw, s: {'valid': True})
    kwargs = client.get_calls[0][1]
    assert kwargs['stream'] and kwargs['headers']['If-Match'] == '"original"'
    assert kwargs['headers']['Range'] == 'bytes=0-59'
    assert first['response_headers']['Last-Modified'] == headers()['Last-Modified']
    assert nf.fetch_one(tmp_path, 'inventory', span(), Client()) == first
    (tmp_path/first['binary_path']).write_bytes(b'z'*60)
    with pytest.raises(ValueError, match='integrity failure'):
        nf.fetch_one(tmp_path, 'inventory', span(), Client())


def test_short_or_oversized_range_is_not_written(tmp_path):
    for size in (59, 61):
        response = range_response(); response.body = b'x'*size
        with pytest.raises(ValueError):
            nf.fetch_one(tmp_path, 'inventory', span(), Client(get=response))
    assert not list(tmp_path.rglob('*.grib2'))


def synthetic_grib(field='temperature'):
    codes = pytest.importorskip('eccodes')
    gid = codes.codes_grib_new_from_samples('regular_ll_sfc_grib2')
    category, parameter, level = {'temperature': (0,0,2), 'relative_humidity': (1,1,2),
                                  'u_wind': (2,2,10), 'v_wind': (2,3,10)}[field]
    try:
        for key, value in {'Ni':1440, 'Nj':721, 'latitudeOfFirstGridPointInDegrees':90.,
            'latitudeOfLastGridPointInDegrees':-90., 'longitudeOfFirstGridPointInDegrees':0.,
            'longitudeOfLastGridPointInDegrees':359.75, 'iDirectionIncrementInDegrees':.25,
            'jDirectionIncrementInDegrees':.25, 'typeOfLevel':'heightAboveGround', 'level':level,
            'parameterCategory':category, 'parameterNumber':parameter, 'dataDate':20210911,
            'dataTime':0, 'forecastTime':48}.items():
            codes.codes_set(gid, key, value)
        codes.codes_set_values(gid, np.zeros(1440*721))
        return codes.codes_get_message(gid)
    finally:
        codes.codes_release(gid)


@pytest.mark.parametrize('field', ['temperature','relative_humidity','u_wind','v_wind'])
def test_real_decoder_validates_original_forecast_field_headers(field):
    raw = synthetic_grib(field)
    target = {**span(), 'bytes':len(raw), 'field':field}
    metadata = nf.validate_grib(raw, target)
    assert metadata['dataDate'] == 20210911 and metadata['endStep'] == 48
    with pytest.raises(ValueError, match='endStep|startStep'):
        nf.validate_grib(raw, {**target, 'lead_hours':49})
    with pytest.raises(ValueError, match='terminator'):
        nf.validate_grib(raw[:-4]+b'0000', target)
    with pytest.raises(ValueError, match='message length'):
        nf.validate_grib(raw+raw, {**target, 'bytes':2*len(raw)})


def test_inventory_stage_freezes_available_ranges_and_terminal_exclusions(tmp_path, monkeypatch):
    good, late = request_row(), deepcopy(request_row())
    late['request_id'] = 'late'
    parent = {'plan_sha256':'parent', 'requests':[good,late]}
    monkeypatch.setattr(nf.planning, 'read_plan', lambda root: parent)
    original = nf.inventory_one
    def worker(root, parent_sha, request):
        h = headers()
        if request['request_id'] == 'late': h['Last-Modified'] = 'Wed, 15 Sep 2021 00:00:00 GMT'
        return original(root, parent_sha, request, Client(Response(200,response_headers=h), Response(200,inventory_bytes())))
    monkeypatch.setattr(nf, 'inventory_one', worker)
    result = nf.inventory(tmp_path, workers=2)
    assert result['complete'] and result['excluded_objects'] == 1
    assert result['exact_field_bytes'] == 240 and result['field_range_requests'] == 4
    assert nf.read_inventory(tmp_path)['inventory_plan_sha256'] == result['inventory_plan_sha256']
    (tmp_path/nf.RAW/'inventory'/'object.idx').write_bytes(b'replaced')
    with pytest.raises(ValueError, match='Frozen inventory file changed'):
        nf.read_inventory(tmp_path)
