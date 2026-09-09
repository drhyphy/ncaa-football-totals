from datetime import datetime
import gzip
import hashlib
import json

import pytest
import requests

from ncaaf_model import availability_role_archive as archive
from ncaaf_model.revision_archive import digest_json

ROSTER = 'https://www.espn.com/college-football/team/roster/_/id/183'
GAMECAST = 'https://www.espn.com/college-football/game/_/gameId/401858208/new-hampshire-syracuse'
BOXSCORE = 'https://www.espn.com/college-football/boxscore/_/gameId/401858208'


class Response:
    def __init__(self, body=b'<html>original page</html>\n', *, status=200, headers=None, chunks=None, error=None):
        self.status_code = status
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.chunks = [body] if chunks is None else chunks
        self.error = error
        self.closed = self.iterated = False

    def iter_content(self, chunk_size):
        self.iterated = True
        assert chunk_size == archive.CHUNK_BYTES
        yield from self.chunks
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class Session:
    def __init__(self, factory, response):
        self.factory, self.response = factory, response
        self.trust_env = True
        self.auth = ('do', 'not send')
        self.proxies = {'https': 'never_send'}
        self.cookies = {'session': 'never_send'}
        self.headers = {'Authorization': 'never_send', 'Cookie': 'never_send'}
        self.closed = False

    def get(self, url, **kwargs):
        assert self.trust_env is False and self.auth is None
        assert not self.proxies and not self.cookies
        assert self.headers == dict(archive.REQUEST_HEADERS)
        assert kwargs == {'headers': dict(archive.REQUEST_HEADERS), 'stream': True, 'timeout': (5., 10.), 'allow_redirects': False}
        self.factory.calls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, *responses):
        self.responses = responses
        self.sessions, self.calls = [], []

    def __call__(self):
        session = Session(self, self.responses[len(self.sessions)])
        self.sessions.append(session)
        return session


def capture(root, response=None, **kwargs):
    return archive.capture_html(root, ROSTER, kind='roster', team_id='183', requester=Factory(response or Response()), **kwargs)


def verify(root, result):
    receipt = result['receipt']
    body = gzip.decompress((root/receipt['body_path']).read_bytes())
    assert body == result['body']
    assert len(body) == receipt['body_size_bytes'] <= receipt['max_body_bytes']
    assert hashlib.sha256(body).hexdigest() == receipt['body_sha256']
    path = root/receipt['receipt_path']
    assert json.loads(path.read_text()) == receipt
    assert path.stem == digest_json({k:v for k,v in receipt.items() if k != 'receipt_path'})
    assert path.parent == root/archive.ARCHIVE/'receipts'
    assert (root/receipt['body_path']).parent == root/archive.ARCHIVE/'bodies'
    return receipt


@pytest.mark.parametrize('url,kind,ids',[(ROSTER,'roster',{'team_id':183}),
    (GAMECAST,'gamecast',{'game_id':'401858208','team_id':183}), (BOXSCORE,'boxscore',{'game_id':401858208})])
def test_literal_supported_routes_keep_exact_html_bytes_and_identity(tmp_path, url, kind, ids):
    # Raw bytes intentionally include whitespace/non-UTF8; transport never JSON
    # parses, decodes/re-encodes, or certifies the page's team/player semantics.
    original=b'<!doctype html>\n<html><script>window.x={};</script>\xff</html>  '
    factory=Factory(Response(original))
    result=archive.capture_html(tmp_path,url,kind=kind,requester=factory,**ids)
    receipt=verify(tmp_path,result)
    assert result['status']=='captured' and result['body']==original
    assert receipt['body_complete'] is True and receipt['body_hash_scope']=='complete_response'
    assert receipt['html_semantics_verified'] is False and not receipt.get('parse_error')
    assert receipt['request']['url']==url and receipt['source_identity']['kind']==kind
    assert factory.calls==[url] and factory.sessions[0].closed and factory.sessions[0].response.closed


@pytest.mark.parametrize('url,kind,ids',[
    (ROSTER.replace('https:','http:'),'roster',{'team_id':183}),
    (ROSTER.replace('https:','HTTPS:'),'roster',{'team_id':183}),
    (ROSTER.replace('www.espn.com','espn.com'),'roster',{'team_id':183}),
    (ROSTER.replace('www.espn.com','www.espn.com.evil.invalid'),'roster',{'team_id':183}),
    (ROSTER.replace('www.espn.com','name:secret@www.espn.com'),'roster',{'team_id':183}),
    (ROSTER.replace('www.espn.com','www.espn.com:443'),'roster',{'team_id':183}),
    (ROSTER+'?token=secret','roster',{'team_id':183}), (ROSTER+'?','roster',{'team_id':183}),
    (ROSTER+'#fragment','roster',{'team_id':183}), (ROSTER+'#','roster',{'team_id':183}),
    (ROSTER+'\n','roster',{'team_id':183}), (ROSTER+'/','roster',{'team_id':183}),
    (ROSTER.replace('/id/183','/id/0183'),'roster',{'team_id':183}),
    (ROSTER.replace('/id/183','/id/%31%38%33'),'roster',{'team_id':183}),
    (ROSTER.replace('/roster/','/x/../roster/'),'roster',{'team_id':183}),
    (ROSTER,'roster',{'team_id':184}), (ROSTER,'roster',{'team_id':183.,'game_id':None}),
    (ROSTER,'roster',{'team_id':True}), (ROSTER,'roster',{'team_id':183,'game_id':401858208}),
    (GAMECAST,'gamecast',{'game_id':401858209}), (GAMECAST.rsplit('/',1)[0],'gamecast',{'game_id':401858208}),
    (GAMECAST+'/../other','gamecast',{'game_id':401858208}),
    (BOXSCORE+'/extra','boxscore',{'game_id':401858208}),
    (BOXSCORE,'roster',{'team_id':183}), (ROSTER,'other',{'team_id':183})])
def test_disallowed_urls_and_identity_types_fail_before_network_or_archive(tmp_path,url,kind,ids):
    factory=Factory(Response())
    with pytest.raises(ValueError): archive.capture_html(tmp_path,url,kind=kind,requester=factory,**ids)
    assert not factory.calls and not factory.sessions
    assert not (tmp_path/archive.ARCHIVE).exists()


def test_same_body_reuses_immutable_content_but_each_receipt_is_distinct(tmp_path):
    first=capture(tmp_path)
    path=tmp_path/first['receipt']['body_path']
    original=path.read_bytes()
    second=capture(tmp_path)
    assert first['receipt']['body_path']==second['receipt']['body_path']
    assert first['receipt']['receipt_path']!=second['receipt']['receipt_path']
    assert path.read_bytes()==original
    verify(tmp_path,first);verify(tmp_path,second)
    assert len(list((tmp_path/archive.ARCHIVE/'bodies').glob('*')))==1


@pytest.mark.parametrize('status',[301,302,307,308,401,403,429,500])
def test_http_failure_and_redirect_retains_body_with_no_retry_or_followup(tmp_path,status):
    factory=Factory(Response(b'<html>unavailable</html>',status=status,headers={'Location':'https://private.invalid?token=secret'}))
    result=archive.capture_html(tmp_path,ROSTER,kind='roster',team_id=183,requester=factory)
    receipt=verify(tmp_path,result)
    assert result['status']=='unavailable' and len(factory.calls)==1
    assert receipt['status_code']==status and receipt['body_complete'] is True
    assert receipt['http_error']==('redirect_refused' if status<400 else 'http_status_not_200')
    assert 'private.invalid' not in json.dumps(receipt)


@pytest.mark.parametrize('failure',[requests.Timeout('secret URL'),requests.ConnectionError('sensitive body')])
def test_transport_exception_retains_empty_failure_receipt_without_error_text(tmp_path,failure):
    result=capture(tmp_path,failure)
    receipt=verify(tmp_path,result)
    assert result['status']=='unavailable' and result['body']==b''
    assert receipt['transport_error']==type(failure).__name__ and receipt['body_complete'] is False
    assert str(failure) not in json.dumps(receipt)


def test_partial_stream_preserves_exact_prefix_and_is_not_success(tmp_path):
    result=capture(tmp_path,Response(chunks=[b'<html>partial'],error=requests.exceptions.ChunkedEncodingError('do not copy')))
    receipt=verify(tmp_path,result)
    assert result['body']==b'<html>partial' and result['status']=='unavailable'
    assert receipt['body_hash_scope']=='incomplete_prefix' and receipt['transport_error']=='ChunkedEncodingError'


def test_decoded_cap_and_declared_cap_preserve_failure_prefixes(tmp_path,monkeypatch):
    monkeypatch.setattr(archive,'MAX_BYTES',8)
    result=capture(tmp_path,Response(chunks=[b'ab',b'0123456789'],headers={'Content-Encoding':'gzip','Content-Length':'3'}))
    receipt=verify(tmp_path,result)
    assert result['body']==b'ab012345' and receipt['observed_decoded_bytes']==12
    assert receipt['capture_error']=='decoded_body_exceeds_limit' and not receipt['body_complete']
    response=Response(b'not read',headers={'Content-Length':'9'})
    result=capture(tmp_path,response)
    assert not response.iterated and result['body']==b''
    assert verify(tmp_path,result)['capture_error']=='declared_body_exceeds_limit'


@pytest.mark.parametrize('ticks,chunks,expected,complete',[
    ([0,31,31],[b'never'],b'',False),
    ([0,1,31,31],[b'partial',b'later'],b'partial',False),
    ([0,1,31],[],b'',True)])
def test_elapsed_checks_after_headers_chunks_and_empty_eof(tmp_path,ticks,chunks,expected,complete):
    values=iter(ticks)
    result=capture(tmp_path,Response(chunks=chunks),monotonic=lambda:next(values))
    receipt=verify(tmp_path,result)
    assert result['status']=='unavailable' and result['body']==expected
    assert receipt['capture_error']=='elapsed_budget_exceeded' and receipt['body_complete'] is complete


def test_explicit_utc_receipts_compare_instants_even_with_different_fractional_precision(tmp_path):
    values=iter(['2026-09-09T10:00:00Z','2026-09-09T10:00:00.500000Z'])
    result=capture(tmp_path,utc_clock=lambda:next(values))
    assert result['status']=='captured' and result['receipt']['capture_error'] is None
    assert result['receipt']['received_at']=='2026-09-09T10:00:00.500000Z'
    reversed_clock=iter(['2026-09-09T10:00:00Z','2026-09-09T09:59:59Z'])
    result=capture(tmp_path,utc_clock=lambda:next(reversed_clock))
    assert result['status']=='unavailable' and result['receipt']['capture_error']=='clock_regressed'
    factory=Factory(Response())
    with pytest.raises(ValueError): archive.capture_html(tmp_path,ROSTER,kind='roster',team_id=183,requester=factory,utc_clock=lambda:datetime(2026,9,9))
    assert not factory.calls


def test_only_safe_headers_are_retained_and_wire_size_is_not_decoded_size(tmp_path):
    headers={key:'original' for key in archive.SAFE_HEADERS}
    headers.update({'content-length':'3','Set-Cookie':'never retain','Authorization':'never retain'})
    result=capture(tmp_path,Response(b'<html>decoded original</html>',headers=headers))
    receipt=verify(tmp_path,result)
    assert receipt['body_size_bytes']>3 and receipt['response_headers']['content_length']=='3'
    assert set(receipt['response_headers'])=={k.replace('-','_') for k in archive.SAFE_HEADERS}
    assert 'never retain' not in json.dumps(receipt)


def test_symlinked_archive_directory_rejected_before_request(tmp_path):
    other=tmp_path/'other';other.mkdir()
    (tmp_path/archive.ARCHIVE).parent.mkdir(parents=True)
    (tmp_path/archive.ARCHIVE).symlink_to(other,target_is_directory=True)
    factory=Factory(Response())
    with pytest.raises(ValueError): archive.capture_html(tmp_path,ROSTER,kind='roster',team_id=183,requester=factory)
    assert not factory.calls and not list(other.iterdir())


def test_existing_content_address_collision_is_not_overwritten(tmp_path):
    first=capture(tmp_path)
    body_path=tmp_path/first['receipt']['body_path']
    body_path.write_bytes(gzip.compress(b'different original'))
    corrupted=body_path.read_bytes()
    with pytest.raises(ValueError,match='Immutable archive collision'): capture(tmp_path)
    assert body_path.read_bytes()==corrupted


def test_corrupt_existing_gzip_does_not_leak_original_prefix_in_errors(tmp_path):
    first=capture(tmp_path)
    body_path=tmp_path/first['receipt']['body_path']
    body_path.write_bytes(b'sensitive incomplete original')
    with pytest.raises(ValueError,match='^Immutable archive collision$'):
        capture(tmp_path)
    assert body_path.read_bytes()==b'sensitive incomplete original'
