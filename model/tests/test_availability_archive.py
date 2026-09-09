import gzip
import hashlib
import json
from pathlib import Path

import pytest
import requests

from ncaaf_model import availability_archive as archive
from ncaaf_model.revision_archive import digest_json


class Response:
    def __init__(self, body=b'{}', status=200, headers=None, chunks=None, error=None):
        self.status_code = status
        self.headers = headers or {'Content-Type': 'application/json'}
        self.chunks = [body] if chunks is None else chunks
        self.error = error
        self.closed = False
        self.iterated = False

    def iter_content(self, chunk_size):
        self.iterated = True
        assert 0 < chunk_size <= archive.CHUNK_BYTES
        yield from self.chunks
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response, factory):
        self.response, self.factory = response, factory
        self.trust_env = True
        self.auth = ('should', 'clear')
        self.proxies = {'https': 'unwanted_proxy'}
        self.cookies = {'session': 'unwanted_cookie'}
        self.headers = {'Authorization': 'unwanted_auth', 'Cookie': 'unwanted_cookie'}
        self.closed = False

    def post(self, url, **kwargs):
        assert self.trust_env is False and self.auth is None
        assert not self.proxies and not self.cookies
        assert self.headers == dict(archive.REQUEST_HEADERS)
        assert kwargs['headers'] == dict(archive.REQUEST_HEADERS)
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        assert kwargs['timeout'] == 30.
        assert set(kwargs) == {'data', 'headers', 'allow_redirects', 'stream', 'timeout'}
        self.factory.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.sessions, self.calls = [], []

    def __call__(self):
        response = self.responses[len(self.sessions)]
        session = Session(response, self)
        self.sessions.append(session)
        return session


def eligible():
    return Response(b'{"public":true}')


def verify_envelope(root, envelope):
    r = envelope['receipt']
    body = gzip.decompress((root/r['body_path']).read_bytes())
    assert hashlib.sha256(body).hexdigest() == r['body_sha256']
    assert len(body) == r['body_size_bytes'] <= r['max_body_bytes']
    receipt = root/r['receipt_path']
    assert json.loads(receipt.read_text()) == r
    assert receipt.stem == digest_json({k:v for k,v in r.items() if k != 'receipt_path'})
    assert r['requested_at'].endswith('Z') and r['received_at'].endswith('Z')
    assert r['requested_at'] <= r['received_at']
    return body


def test_exact_public_sequence_fresh_sessions_and_original_bytes(tmp_path):
    original = b' {"1182":{"ReportType":"Report Pending","games":[]}}\n'
    factory = Factory(eligible(), Response(original))
    result = archive.capture_current(tmp_path, requester=factory)
    assert result['status'] == 'captured' and result['request_count'] == 2
    assert [u for u,_ in factory.calls] == [archive.PUBLIC_ACCESS_URL, archive.CURRENT_URL]
    assert [json.loads(k['data']) for _,k in factory.calls] == [dict(archive.PUBLIC_ACCESS_BODY), dict(archive.CURRENT_BODY)]
    assert len(factory.sessions) == 2 and all(s.closed for s in factory.sessions)
    assert all(s.response.closed for s in factory.sessions)
    assert verify_envelope(tmp_path, result['current']) == original
    assert result['current']['receipt']['body_complete'] is True
    for e in result['envelopes']:
        r=e['receipt']; raw=factory.calls[result['envelopes'].index(e)][1]['data']
        assert r['request']['method'] == 'POST' and r['request']['body_utf8'].encode() == raw
        assert r['request']['body_sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('body',[b'{"public":false}', b'{"public":1}', b'{}', b'[]', b'null'])
def test_public_true_is_explicit_boolean_and_current_is_not_requested(tmp_path,body):
    factory=Factory(Response(body))
    result=archive.capture_current(tmp_path, requester=factory)
    assert result['status']=='public_access_not_granted'
    assert result['request_count']==1 and result['current'] is None
    verify_envelope(tmp_path,result['public_access'])


@pytest.mark.parametrize('body',[b'not json',b'{"public":true,"public":false}',b'{"public":NaN}',b'{"public":Infinity}',b'{"public":1e999}',b'\xff'])
def test_invalid_or_ambiguous_access_json_is_archived_but_cannot_authorize(tmp_path,body):
    result=archive.capture_current(tmp_path,requester=Factory(Response(body)))
    assert result['status']=='public_access_unavailable' and result['request_count']==1
    e=result['public_access'];assert e['payload'] is None and e['receipt']['parse_error']
    assert e['receipt']['body_complete'] is True and verify_envelope(tmp_path,e)==body


@pytest.mark.parametrize('status',[301,302,307,308,401,403,429,500])
def test_http_errors_and_redirects_preserve_original_without_followup(tmp_path,status):
    factory=Factory(Response(b'{"public":true}',status,{'Location':'https://private.invalid/path'}))
    result=archive.capture_current(tmp_path,requester=factory)
    assert result['status']=='public_access_unavailable' and len(factory.calls)==1
    r=result['public_access']['receipt'];assert r['status_code']==status
    assert 'location' not in r['response_headers']
    assert r['http_error']==('redirect_refused' if 300<=status<400 else 'http_status_not_200')
    verify_envelope(tmp_path,result['public_access'])


@pytest.mark.parametrize('failure',[requests.Timeout('sensitive detail'),requests.ConnectionError('private URL')])
def test_transport_failure_has_empty_incomplete_prefix_and_closed_session(tmp_path,failure):
    factory=Factory(failure)
    result=archive.capture_current(tmp_path,requester=factory)
    e=result['public_access'];r=e['receipt']
    assert r['status_code'] is None and r['transport_error']==type(failure).__name__
    assert r['body_complete'] is False and r['body_hash_scope']=='incomplete_prefix'
    assert verify_envelope(tmp_path,e)==b'' and factory.sessions[0].closed
    assert str(failure) not in json.dumps(r)


def test_interrupted_stream_preserves_exact_prefix_even_if_it_is_valid_json(tmp_path):
    response=Response(chunks=[b'{"public":true}'],error=requests.exceptions.ChunkedEncodingError('interrupted'))
    result=archive.capture_current(tmp_path,requester=Factory(response))
    e=result['public_access'];r=e['receipt']
    assert r['body_complete'] is False and e['payload'] is None
    assert r['transport_error']=='ChunkedEncodingError' and result['request_count']==1
    assert verify_envelope(tmp_path,e)==b'{"public":true}'


def test_access_and_full_slate_have_separate_limits(tmp_path,monkeypatch):
    monkeypatch.setattr(archive,'ACCESS_MAX_BYTES',16)
    monkeypatch.setattr(archive,'CURRENT_MAX_BYTES',32)
    result=archive.capture_current(tmp_path,requester=Factory(eligible(),Response(b'{"payload":"more than sixteen"}')))
    assert result['status']=='captured'
    assert result['public_access']['receipt']['max_body_bytes']==16
    assert result['current']['receipt']['max_body_bytes']==32
    assert archive.TIMEOUT_SECONDS==30.


def test_oversize_decoded_prefix_is_not_json_parsed_or_used(tmp_path,monkeypatch):
    monkeypatch.setattr(archive,'ACCESS_MAX_BYTES',8)
    response=Response(chunks=[b'{}',b' '*20],headers={'Content-Encoding':'gzip','Content-Length':'3'})
    result=archive.capture_current(tmp_path,requester=Factory(response))
    e=result['public_access'];r=e['receipt']
    assert r['capture_error']=='decoded_body_exceeds_limit'
    assert r['body_complete'] is False and r['observed_decoded_bytes']==22
    assert e['payload'] is None and verify_envelope(tmp_path,e)==b'{}'+b' '*6
    assert response.closed


def test_declared_oversize_refused_before_consuming_body(tmp_path,monkeypatch):
    monkeypatch.setattr(archive,'ACCESS_MAX_BYTES',8)
    response=Response(b'never read',headers={'Content-Length':'9'})
    result=archive.capture_current(tmp_path,requester=Factory(response))
    e=result['public_access'];assert not response.iterated
    assert e['receipt']['capture_error']=='declared_body_exceeds_limit'
    assert verify_envelope(tmp_path,e)==b'' and e['receipt']['body_complete'] is False


def test_elapsed_budget_after_headers_prevents_stream_read(tmp_path,monkeypatch):
    ticks=iter([0.,31.,31.]);monkeypatch.setattr(archive.time,'monotonic',lambda:next(ticks))
    response=eligible();result=archive.capture_current(tmp_path,requester=Factory(response))
    assert not response.iterated and result['request_count']==1
    assert result['public_access']['receipt']['capture_error']=='elapsed_budget_exceeded'


def test_elapsed_budget_between_chunks_marks_prefix_incomplete(tmp_path,monkeypatch):
    ticks=iter([0.,1.,31.,31.]);monkeypatch.setattr(archive.time,'monotonic',lambda:next(ticks))
    result=archive.capture_current(tmp_path,requester=Factory(Response(chunks=[b'{"public":true}',b' later'])))
    e=result['public_access'];assert e['payload'] is None
    assert e['receipt']['capture_error']=='elapsed_budget_exceeded'
    assert verify_envelope(tmp_path,e)==b'{"public":true}'


def test_only_safe_response_headers_are_retained(tmp_path):
    headers={'Date':'Wed, 09 Sep 2026 08:00:00 GMT','ETag':'original-tag','Last-Modified':'original-date',
             'Cache-Control':'max-age=60','Age':'12','Content-Type':'application/json','Content-Length':'15',
             'Content-Encoding':'gzip','Transfer-Encoding':'chunked','Set-Cookie':'never publish','Authorization':'never publish'}
    e=archive.capture_current(tmp_path,requester=Factory(Response(b'{"public":false}',headers=headers)))['public_access']
    assert set(e['receipt']['response_headers'])=={x.replace('-','_') for x in archive.SAFE_HEADERS}
    assert 'never publish' not in json.dumps(e['receipt'])


@pytest.mark.parametrize('body,status',[(b'html',200),(b'[]',200),(b'{"error":"unavailable"}',503)])
def test_current_failure_keeps_both_receipts_without_retry(tmp_path,body,status):
    factory=Factory(eligible(),Response(body,status))
    result=archive.capture_current(tmp_path,requester=factory)
    assert result['status']=='current_unavailable' and len(factory.calls)==2
    assert verify_envelope(tmp_path,result['current'])==body


def test_repeated_capture_reuses_body_but_retains_distinct_receipts(tmp_path):
    first=archive.capture_current(tmp_path,requester=Factory(eligible(),Response(b'{}')))
    second=archive.capture_current(tmp_path,requester=Factory(eligible(),Response(b'{}')))
    for a,b in zip(first['envelopes'],second['envelopes']):
        assert a['receipt']['body_path']==b['receipt']['body_path']
        assert a['receipt']['receipt_path']!=b['receipt']['receipt_path']
        verify_envelope(tmp_path,a);verify_envelope(tmp_path,b)
    assert len(list((tmp_path/'data/runtime/availability/receipts').glob('*.json')))==4


def test_gzip_runtime_variant_preserved_but_changed_original_rejected(tmp_path):
    result=archive.capture_current(tmp_path,requester=Factory(Response(b'{"public":false}')))
    path=tmp_path/result['public_access']['receipt']['body_path']
    variant=bytearray(path.read_bytes());variant[9]=3;path.write_bytes(variant)
    archive.capture_current(tmp_path,requester=Factory(Response(b'{"public":false}')))
    assert path.read_bytes()==bytes(variant)
    path.write_bytes(gzip.compress(b'corrupted'))
    with pytest.raises(ValueError,match='collision'):
        archive.capture_current(tmp_path,requester=Factory(Response(b'{"public":false}')))


def test_archive_path_cannot_escape_model_root_or_use_root_itself(tmp_path):
    for path in [tmp_path.parent/'outside',tmp_path,Path('../outside')]:
        factory=Factory(eligible())
        with pytest.raises(ValueError,match='beneath'):
            archive.capture_current(tmp_path,archive=path,requester=factory)
        assert not factory.calls
    (tmp_path/'link').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(ValueError,match='beneath'):
        archive.capture_current(tmp_path,archive=tmp_path/'link'/'escape',requester=Factory(eligible()))


@pytest.mark.parametrize('url,body',[
    ('https://app.hdintelligence.com/api/get-publish',dict(archive.CURRENT_BODY)),
    (archive.CURRENT_URL,{**archive.CURRENT_BODY,'organization':'SEC'}),
    (archive.PUBLIC_ACCESS_URL,{**archive.PUBLIC_ACCESS_BODY,'type_param':'archive'}),
])
def test_unknown_route_or_parameters_fail_before_request(tmp_path,url,body):
    factory=Factory(eligible())
    with pytest.raises(ValueError,match='exact public'):
        archive._capture(tmp_path,tmp_path/'test',url,body,archive.CURRENT_MAX_BYTES,factory)
    assert not factory.calls


def test_default_transport_creates_fresh_sessions_without_live_network(tmp_path,monkeypatch):
    factory=Factory(eligible(),Response(b'{}'))
    monkeypatch.setattr(archive.requests,'Session',factory)
    result=archive.capture_current(tmp_path)
    assert result['status']=='captured' and len(factory.sessions)==2


def test_literal_caps_match_full_slate_design():
    assert archive.ACCESS_MAX_BYTES==1024*1024
    assert archive.CURRENT_MAX_BYTES==64*1024*1024


def test_regressed_utc_clock_is_retained_but_not_usable(tmp_path,monkeypatch):
    times=iter(['2026-09-09T08:01:00Z','2026-09-09T08:00:00Z'])
    monkeypatch.setattr(archive,'utc_now',lambda:next(times))
    result=archive.capture_current(tmp_path,requester=Factory(eligible()))
    receipt=result['public_access']['receipt']
    assert receipt['capture_error']=='utc_clock_regressed'
    assert receipt['received_at']<receipt['requested_at']
    assert result['request_count']==1 and result['status']=='public_access_unavailable'


def test_archive_relative_subdirectory_is_anchored_and_capture_calls_are_not_cached(tmp_path):
    factory=Factory(Response(b'{"public":false}'))
    result=archive.capture_current(tmp_path,archive=Path('data/runtime/availability/unit'),requester=factory)
    path=result['public_access']['receipt']['receipt_path']
    assert path.startswith('data/runtime/availability/unit/receipts/')
    verify_envelope(tmp_path,result['public_access'])
