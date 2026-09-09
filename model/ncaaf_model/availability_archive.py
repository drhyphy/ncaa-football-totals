"""Fixed public ACC current-report acquisition; no player/status interpretation.

No automatic execution, retries, redirects, cookies, credentials or environment
proxy settings. The optional requester is a zero-argument Session factory used
by offline tests; a new session is made for each public POST. ``root`` is the
model directory. Original decoded response bytes, including incomplete prefixes,
are retained independently of any later semantic/report-version parser.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from types import MappingProxyType
from uuid import uuid4

import requests

from .revision_archive import immutable_bytes, immutable_json, digest_json

VERSION = 'acc-availability-archive-v1'
PUBLIC_ACCESS_URL = 'https://app.hdintelligence.com/api/public-load'
CURRENT_URL = 'https://app.hdintelligence.com/api/get-publish-public'
PUBLIC_ACCESS_BODY = MappingProxyType({
    'conference_param': 'ACC', 'sport_param': 'Football', 'type_param': 'report',
    'referrer': 'https://theacc.com/sports/2025/8/28/availability-reporting-football.aspx',
    'source': 'ACC',
})
CURRENT_BODY = MappingProxyType({'sport': 'Football', 'organization': 'ACC', 'conference': 'ACC'})
ACCESS_MAX_BYTES = 1024*1024
CURRENT_MAX_BYTES = 64*1024*1024
TIMEOUT_SECONDS = 30.
CHUNK_BYTES = 64*1024
REQUEST_HEADERS = MappingProxyType({'Accept': 'application/json', 'Content-Type': 'application/json',
                                    'User-Agent': 'NCAAF-public-availability-research/1.0'})
SAFE_HEADERS = ('date', 'etag', 'last-modified', 'cache-control', 'age', 'content-type',
                'content-length', 'content-encoding', 'transfer-encoding')


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _strict_json(body):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate_json_key')
            value[key] = item
        return value

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite_json_number')
        return result

    def constant(_):
        raise ValueError('nonfinite_json_constant')

    return json.loads(body, object_pairs_hook=pairs, parse_float=floating, parse_constant=constant)


def _archive_path(root, archive):
    root = Path(root).resolve()
    path = (root/'data/runtime/availability' if archive is None else Path(archive))
    if not path.is_absolute():
        path = root/path
    path = path.resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Availability archive must remain beneath the model root')
    return root, path


def _capture(root, archive, url, body, cap, requester):
    """Internal fixed-route POST; unknown endpoints/parameters fail before I/O."""
    allowed = {PUBLIC_ACCESS_URL: (dict(PUBLIC_ACCESS_BODY), ACCESS_MAX_BYTES),
               CURRENT_URL: (dict(CURRENT_BODY), CURRENT_MAX_BYTES)}
    if url not in allowed or (body, cap) != allowed[url]:
        raise ValueError('Only exact public ACC endpoints and payloads are allowed')
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    receipt = {
        'schema_version': 'availability-http-receipt-v1', 'archive_writer_version': VERSION,
        'request_id': uuid4().hex, 'requested_at': utc_now(), 'received_at': None,
        'request': {'method': 'POST', 'url': url, 'json': dict(body),
                    'body_utf8': encoded.decode('utf-8'), 'body_sha256': hashlib.sha256(encoded).hexdigest(),
                    'headers': dict(REQUEST_HEADERS)},
        'status_code': None, 'response_headers': {}, 'body_sha256': None, 'body_path': None,
        'body_size_bytes': 0, 'body_complete': False, 'observed_decoded_bytes': 0,
        'body_representation': 'requests.iter_content bytes after HTTP transfer/content decoding; original unparsed representation',
        'body_hash_scope': 'incomplete_prefix', 'transport_error': None, 'parse_error': None,
        'capture_error': None, 'max_body_bytes': cap, 'timeout_seconds': TIMEOUT_SECONDS,
        'timeout_semantics': 'Connect/read inactivity timeout plus elapsed budget checked after headers and between decoded chunks; not a strict total wall-clock guarantee.',
        'redirects_allowed': False, 'retries': 0, 'credentials_sent': False,
        'cookies_sent': False, 'environment_settings_used': False,
    }
    started = time.monotonic()
    session = response = None
    chunks, count = [], 0
    payload = None
    try:
        session = (requester or requests.Session)()
        session.trust_env = False
        session.auth = None
        session.proxies.clear()
        session.cookies.clear()
        session.headers.clear()
        session.headers.update(REQUEST_HEADERS)
        response = session.post(url, data=encoded, headers=dict(REQUEST_HEADERS),
                                stream=True, timeout=TIMEOUT_SECONDS, allow_redirects=False)
        receipt['status_code'] = int(response.status_code)
        receipt['response_headers'] = {str(k).lower().replace('-', '_'): str(v)
                                       for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS}
        content_length = receipt['response_headers'].get('content_length')
        if time.monotonic()-started >= TIMEOUT_SECONDS:
            receipt['capture_error'] = 'elapsed_budget_exceeded'
        elif content_length and content_length.isdigit() and int(content_length) > cap:
            receipt['capture_error'] = 'declared_body_exceeds_limit'
        else:
            for chunk in response.iter_content(chunk_size=min(CHUNK_BYTES, cap+1)):
                if not isinstance(chunk, bytes):
                    raise TypeError('nonbyte_stream_chunk')
                receipt['observed_decoded_bytes'] += len(chunk)
                remaining = cap-count
                if chunk:
                    chunks.append(chunk[:remaining])
                    count += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    receipt['capture_error'] = 'decoded_body_exceeds_limit'
                    break
                if time.monotonic()-started >= TIMEOUT_SECONDS:
                    receipt['capture_error'] = 'elapsed_budget_exceeded'
                    break
            else:
                receipt['body_complete'] = True
    except Exception as error:
        # Class and stage are retained; exception text may contain environment
        # paths/URLs and is deliberately not copied into a public receipt.
        receipt['transport_error'] = type(error).__name__
    finally:
        receipt['received_at'] = utc_now()
        receipt['elapsed_seconds'] = max(0., time.monotonic()-started)
        if receipt['received_at'] < receipt['requested_at']:
            receipt['capture_error'] = receipt['capture_error'] or 'utc_clock_regressed'
        for resource in (response, session):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    receipt.setdefault('cleanup_errors', []).append(type(error).__name__)
    original = b''.join(chunks)
    receipt['body_size_bytes'] = len(original)
    receipt['body_sha256'] = hashlib.sha256(original).hexdigest()
    receipt['body_hash_scope'] = 'complete_response' if receipt['body_complete'] else 'incomplete_prefix'
    target = archive/'bodies'/(receipt['body_sha256']+'.body.gz')
    immutable_bytes(target, gzip.compress(original, compresslevel=9, mtime=0),
                    equivalent=lambda existing: gzip.decompress(existing) == original)
    receipt['body_path'] = target.relative_to(root).as_posix()
    if receipt['body_complete']:
        try:
            payload = _strict_json(original)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            receipt['parse_error'] = type(error).__name__
    receipt['http_error'] = None if receipt['status_code'] == 200 else (
        'redirect_refused' if receipt['status_code'] is not None and 300 <= receipt['status_code'] < 400 else 'http_status_not_200')
    receipt_path = archive/'receipts'/(digest_json(receipt)+'.json')
    receipt['receipt_path'] = receipt_path.relative_to(root).as_posix()
    immutable_json(receipt_path, receipt)
    return {'payload': payload, 'receipt': receipt}


def _usable(envelope):
    r = envelope['receipt']
    return (r['status_code'] == 200 and r['body_complete'] is True and
            not any(r[key] for key in ('transport_error', 'parse_error', 'capture_error')))


def capture_current(root, archive=None, requester=None):
    """Capture public eligibility, then the current ACC report only if allowed.

    Returns ``status``, ``public_access``, nullable ``current``, all original
    ``envelopes`` and ``request_count``. ``captured`` means an intact HTTP200 JSON
    object was received; it makes NO claim about report phase, completeness of
    player rows, issuance, game identity or player availability. No response is
    cached between calls. The Session factory is solely a transport injection;
    production callers should omit it.
    """
    root, archive = _archive_path(root, archive)
    access = _capture(root, archive, PUBLIC_ACCESS_URL, dict(PUBLIC_ACCESS_BODY), ACCESS_MAX_BYTES, requester)
    result = {'status': 'public_access_unavailable', 'public_access': access, 'current': None,
              'envelopes': [access], 'request_count': 1}
    if not _usable(access):
        return result
    if not isinstance(access['payload'], dict) or access['payload'].get('public') is not True:
        result['status'] = 'public_access_not_granted'
        return result
    current = _capture(root, archive, CURRENT_URL, dict(CURRENT_BODY), CURRENT_MAX_BYTES, requester)
    result.update(current=current, envelopes=[access, current], request_count=2,
                  status='captured' if _usable(current) and isinstance(current['payload'], dict) else 'current_unavailable')
    return result
