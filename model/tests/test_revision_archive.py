from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json

import pytest
import requests

from ncaaf_model.revision_archive import ArchiveClient, immutable_bytes, load_envelope


class Session:
    def __init__(self, body=b'{"value": 1}', status=200):
        self.body, self.status = body, status

    def get(self, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        self.kwargs = kwargs
        response = requests.Response()
        response.status_code, response._content = self.status, self.body
        response.headers['Content-Type'] = 'application/json'
        response.headers['X-RateLimit-Remaining'] = '81'
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = '40'
        return response


def test_original_body_and_receipt_integrity(tmp_path):
    original = b' {"value": 1}\n'
    client = ArchiveClient(tmp_path, session=Session(original))
    result = client.fetch('https://example.com/data', {'id': 1})
    receipt = result['receipt']
    assert gzip.decompress((tmp_path / receipt['body_path']).read_bytes()) == original
    assert receipt['body_sha256'] == hashlib.sha256(original).hexdigest()
    assert receipt['response_headers']['content_length'] == '40'
    assert receipt['body_size_bytes'] == len(original)
    assert load_envelope(tmp_path, receipt['receipt_path']) == result
    path = tmp_path / receipt['receipt_path']
    changed = json.loads(path.read_text())
    changed['received_at'] = '2026-01-01T00:00:00Z'
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='receipt hash'):
        load_envelope(tmp_path, receipt['receipt_path'])


def test_credential_echo_is_hashed_but_never_published(tmp_path):
    secret = 'test-secret+opaque'
    client = ArchiveClient(tmp_path, session=Session(('error ' + secret).encode(), 401))
    result = client.fetch('https://example.com/data', secret_params={'apiKey': secret})
    receipt = result['receipt']
    assert receipt['body_withheld'] and receipt['body_path'] is None
    assert result['payload'] is None
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    assert receipt['status_code'] == 401 and receipt['body_sha256']


def test_http_error_original_body_and_network_error_are_explicit(tmp_path):
    client = ArchiveClient(tmp_path, session=Session(b'not json', 503))
    receipt = client.fetch('https://example.com/data')['receipt']
    assert receipt['status_code'] == 503 and receipt['transport_error'] == 'invalid_json'
    assert gzip.decompress((tmp_path / receipt['body_path']).read_bytes()) == b'not json'
    class Broken:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError('https://example.com?apiKey=hidden')
    client.session = Broken()
    receipt = client.fetch('https://example.com/data')['receipt']
    assert receipt['status_code'] is None and receipt['transport_error'] == 'ConnectionError'
    assert 'hidden' not in json.dumps(receipt)


def test_content_addressed_write_is_concurrent_and_immutable(tmp_path):
    path = tmp_path / 'blob'
    body = b'original' * 100_000
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: immutable_bytes(path, body), range(16)))
    assert path.read_bytes() == body
    with pytest.raises(ValueError, match='collision'):
        immutable_bytes(path, b'changed')
    assert path.read_bytes() == body


def test_existing_original_body_survives_different_runtime_gzip_header(tmp_path):
    original = b'{"same": true}'
    client = ArchiveClient(tmp_path, session=Session(original))
    first = client.fetch('https://example.com/data')['receipt']
    path = tmp_path / first['body_path']
    variant = bytearray(path.read_bytes())
    variant[9] = 3  # Python 3.11/3.12 zlib-derived Unix OS byte, vs 3.13's 255.
    path.write_bytes(variant)
    second = client.fetch('https://example.com/data')['receipt']
    assert first['body_sha256'] == second['body_sha256']
    assert path.read_bytes() == bytes(variant)
    assert load_envelope(tmp_path, second['receipt_path'])['payload'] == {'same': True}
    with pytest.raises(ValueError, match='collision'):
        immutable_bytes(path, b'changed')
    assert path.read_bytes() == bytes(variant)


@pytest.mark.parametrize('url,params', [
    ('https://example.com/data?apiKey=secret', {}),
    ('https://user@example.com/data', {}),
    ('http://example.com/data', {}),
    ('https://example.com/data', {'apiKey': 'secret'}),
])
def test_public_request_cannot_contain_credentials(tmp_path, url, params):
    with pytest.raises(ValueError):
        ArchiveClient(tmp_path, session=Session()).fetch(url, params)
