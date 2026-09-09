"""Archive provided public ESPN role-page URLs; never select or parse players.

``root`` is the model directory. Callers must establish that each literal URL
was observed in its permitted source; URL validation here does not establish
link provenance or validate the identities inside a returned page. No HTTP is
performed on import. Transport success is not semantic role evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import math
from pathlib import Path
import re
import time
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4
import zlib

import requests

from .revision_archive import digest_json, immutable_bytes, immutable_json

VERSION = 'availability-role-html-archive-v1'
SCHEMA = 'availability-role-http-receipt-v1'
ARCHIVE = Path('data/runtime/availability_roles')
MAX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
TIMEOUT = (5., 10.)
ELAPSED_SECONDS = 30.
REQUEST_HEADERS = MappingProxyType({'Accept': 'text/html,application/xhtml+xml', 'User-Agent': 'curl/8.7.1'})
SAFE_HEADERS = ('date', 'etag', 'last-modified', 'cache-control', 'age', 'content-type',
                'content-length', 'content-encoding', 'transfer-encoding')


def _identifier(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch('[1-9][0-9]*', value):
        raise ValueError('Positive canonical team/game ID required')
    return value


def validate_url(url, *, kind, team_id=None, game_id=None):
    """Validate exact public routes without constructing/replacing their slugs."""
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ValueError('Literal ASCII ESPN URL required')
    parsed = urlsplit(url)
    if (not url.startswith('https://www.espn.com/') or parsed.scheme != 'https' or parsed.netloc != 'www.espn.com' or parsed.query or parsed.fragment
            or '?' in url or '#' in url or '%' in url or '\\' in url):
        raise ValueError('Only exact credential-free HTTPS ESPN origins and paths are allowed')
    team = _identifier(team_id) if team_id is not None else None
    game = _identifier(game_id) if game_id is not None else None
    if kind == 'roster':
        accepted = team is not None and game is None and parsed.path == f'/college-football/team/roster/_/id/{team}'
    elif kind == 'gamecast':
        accepted = game is not None and re.fullmatch(r'/college-football/game/_/gameId/'+game+r'/[a-z0-9]+(?:-[a-z0-9]+)*', parsed.path)
    elif kind == 'boxscore':
        accepted = game is not None and parsed.path == f'/college-football/boxscore/_/gameId/{game}'
    else:
        accepted = False
    if not accepted:
        raise ValueError('Unsupported role URL kind, literal route or ID')
    return {'kind': kind, 'team_id': team, 'game_id': game}


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('UTC clock must supply a timezone-aware timestamp')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _monotonic(clock):
    value = float(clock())
    if not math.isfinite(value):
        raise ValueError('Monotonic clock must be finite')
    return value


def _namespace(root):
    root = Path(root).resolve()
    for suffix in (Path(), Path('bodies'), Path('receipts')):
        expected = root / ARCHIVE / suffix
        if expected.resolve() != expected:
            raise ValueError('Role archive namespace cannot traverse symlinks')
    return root


def _target(root, directory, name):
    _namespace(root)
    path = root / ARCHIVE / directory / name
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('Role archive target cannot traverse symlinks')
    return path


def _same_body(compressed, body):
    # A corrupt old gzip can include original bytes in its exception message.
    # Treat it as an immutable collision without propagating that content.
    try:
        return gzip.decompress(compressed) == body
    except (OSError, EOFError, zlib.error):
        return False


def capture_html(root, url, *, kind, team_id=None, game_id=None, requester=None, utc_clock=None, monotonic=None):
    """Return status, immutable receipt and original decoded bytes (even failures).

    ``requester`` is a zero-argument Session factory; ``utc_clock`` returns an
    aware datetime or ISO timestamp, and ``monotonic`` returns finite seconds.
    All are optional testing injections. ``captured`` means complete HTTP 200
    transfer only: HTML/content/role validation remains the caller's task.
    """
    identity = validate_url(url, kind=kind, team_id=team_id, game_id=game_id)
    root = _namespace(root)
    utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
    monotonic = monotonic or time.monotonic
    requested = _utc(utc_clock())
    started = _monotonic(monotonic)
    receipt = {'schema_version': SCHEMA, 'archive_writer_version': VERSION, 'request_id': uuid4().hex,
        'requested_at': requested, 'received_at': None,
        'request': {'method': 'GET', 'url': url, 'headers': dict(REQUEST_HEADERS)}, 'source_identity': identity,
        'identity_scope': 'URL identity only; caller must verify observed-link provenance and embedded page identities.',
        'status_code': None, 'response_headers': {}, 'body_sha256': None, 'body_path': None,
        'body_size_bytes': 0, 'observed_decoded_bytes': 0, 'body_complete': False,
        'body_representation': 'requests.iter_content bytes after HTTP transfer/content decoding; original unparsed HTML representation',
        'body_hash_scope': 'incomplete_prefix', 'transport_error': None, 'capture_error': None, 'http_error': None,
        'max_body_bytes': MAX_BYTES, 'connect_read_timeout_seconds': list(TIMEOUT), 'elapsed_budget_seconds': ELAPSED_SECONDS,
        'timeout_semantics': 'Connect/read inactivity timeouts plus elapsed checks after headers, between chunks and at receipt; not a strict total wall-clock guarantee.',
        'redirects_allowed': False, 'retries': 0, 'credentials_sent': False, 'cookies_sent': False,
        'environment_settings_used': False, 'html_semantics_verified': False}
    response = session = None
    chunks, count = [], 0
    try:
        session = (requester or requests.Session)()
        session.trust_env = False
        session.auth = None
        session.proxies.clear()
        session.cookies.clear()
        session.headers.clear()
        session.headers.update(REQUEST_HEADERS)
        response = session.get(url, headers=dict(REQUEST_HEADERS), stream=True, timeout=TIMEOUT, allow_redirects=False)
        receipt['status_code'] = int(response.status_code)
        receipt['response_headers'] = {str(k).lower().replace('-', '_'): str(v)
            for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS}
        declared = receipt['response_headers'].get('content_length')
        if _monotonic(monotonic)-started >= ELAPSED_SECONDS:
            receipt['capture_error'] = 'elapsed_budget_exceeded'
        elif declared and declared.isdigit() and int(declared) > MAX_BYTES:
            receipt['capture_error'] = 'declared_body_exceeds_limit'
        else:
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                if not isinstance(chunk, bytes):
                    raise TypeError('nonbyte_stream_chunk')
                receipt['observed_decoded_bytes'] += len(chunk)
                remaining = MAX_BYTES-count
                if chunk:
                    chunks.append(chunk[:remaining])
                    count += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    receipt['capture_error'] = 'decoded_body_exceeds_limit'
                    break
                if _monotonic(monotonic)-started >= ELAPSED_SECONDS:
                    receipt['capture_error'] = 'elapsed_budget_exceeded'
                    break
            else:
                receipt['body_complete'] = True
    except Exception as error:
        # Never retain exception text: it can contain URLs or response content.
        receipt['transport_error'] = type(error).__name__
    finally:
        receipt['received_at'] = _utc(utc_clock())
        elapsed = _monotonic(monotonic)-started
        receipt['elapsed_seconds'] = max(0., elapsed)
        if elapsed < 0 or datetime.fromisoformat(receipt['received_at'].replace('Z', '+00:00')) < datetime.fromisoformat(requested.replace('Z', '+00:00')):
            receipt['capture_error'] = receipt['capture_error'] or 'clock_regressed'
        elif elapsed >= ELAPSED_SECONDS:
            receipt['capture_error'] = receipt['capture_error'] or 'elapsed_budget_exceeded'
        for resource in (response, session):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    receipt.setdefault('cleanup_errors', []).append(type(error).__name__)
    body = b''.join(chunks)
    receipt['body_size_bytes'] = len(body)
    receipt['body_sha256'] = hashlib.sha256(body).hexdigest()
    receipt['body_hash_scope'] = 'complete_response' if receipt['body_complete'] else 'incomplete_prefix'
    target = _target(root, 'bodies', receipt['body_sha256']+'.body.gz')
    immutable_bytes(target, gzip.compress(body, compresslevel=9, mtime=0), equivalent=lambda old: _same_body(old, body))
    receipt['body_path'] = target.relative_to(root).as_posix()
    receipt['http_error'] = None if receipt['status_code'] == 200 else (
        'redirect_refused' if receipt['status_code'] is not None and 300 <= receipt['status_code'] < 400 else 'http_status_not_200')
    path = _target(root, 'receipts', digest_json(receipt)+'.json')
    receipt['receipt_path'] = path.relative_to(root).as_posix()
    immutable_json(path, receipt)
    captured = receipt['status_code'] == 200 and receipt['body_complete'] and not any(receipt[k] for k in ('transport_error', 'capture_error', 'http_error'))
    return {'status': 'captured' if captured else 'unavailable', 'receipt': receipt, 'body': body}
