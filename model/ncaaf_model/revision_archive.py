"""Immutable, credential-safe original HTTP bodies for prospective research."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

import requests

WRITER_VERSION = "revision-archive-v2-portable-gzip"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def immutable_bytes(path: Path, body: bytes, *, equivalent=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-archive-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = path.read_bytes()
            if existing != body and (equivalent is None or not equivalent(existing)):
                raise ValueError("Immutable archive collision")
    finally:
        temporary.unlink(missing_ok=True)


def immutable_json(path: Path, payload) -> None:
    immutable_bytes(path, (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


class ArchiveClient:
    """No retries, redirects, inferred timestamps, or credential-bearing logs.

    Body bytes are retained exactly as returned by requests after HTTP transfer
    decoding. A credential echo is hashed but withheld from the public archive.
    """

    def __init__(self, root: Path, archive: Path | None = None, timeout=30., session=None):
        self.root = root.resolve()
        self.archive = archive or self.root / "data/runtime/weather_revisions"
        self.timeout = timeout
        self.session = session
        self.receipts = []
        self.lock = threading.Lock()

    def fetch(self, url, params=None, secret_params=None, purpose="research"):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Archive URL must be a credential-free HTTPS origin and path")
        public_params = dict(params or {})
        secrets = dict(secret_params or {})
        if set(public_params) & set(secrets) or any(k.lower() in {"apikey", "api_key", "key", "token", "authorization"} for k in public_params):
            raise ValueError("Credentials belong only in secret_params")
        needles = [form.encode() for value in secrets.values() if value
                   for form in {str(value), quote(str(value), safe=""), quote_plus(str(value))}]
        public_request = {"url": url, "params": public_params}
        if any(n in json.dumps(public_request).encode() for n in needles):
            raise ValueError("Credential found in public request")
        receipt = {"schema_version": "raw-http-receipt-v1", "purpose": purpose,
                   "archive_writer_version": WRITER_VERSION,
                   "requested_at": timestamp(), "received_at": None, "request": public_request,
                   "status_code": None, "response_headers": {}, "body_sha256": None,
                   "body_path": None, "transport_error": None, "body_withheld": False}
        payload = None
        try:
            requester = self.session or requests
            response = requester.get(url, params={**public_params, **secrets}, timeout=self.timeout,
                                     headers={"User-Agent": "curl/8.7.1", "Accept": "application/json,text/plain,*/*"},
                                     allow_redirects=False)
            body = response.content
            receipt["received_at"] = timestamp()
            receipt["status_code"] = response.status_code
            receipt["body_sha256"] = hashlib.sha256(body).hexdigest()
            receipt["body_size_bytes"] = len(body)
            receipt["body_representation"] = "requests.content after HTTP transfer/content decoding; original unparsed representation bytes"
            for key in ("Date", "ETag", "Last-Modified", "Content-Type", "Content-Encoding", "Content-Length", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
                value = response.headers.get(key)
                if value is not None and not any(n in str(value).encode() for n in needles):
                    receipt["response_headers"][key.lower().replace("-", "_")] = value
            if any(n in body for n in needles):
                receipt["body_withheld"] = True
                receipt["transport_error"] = "credential_echo_withheld"
            else:
                path = self.archive / "bodies" / f"{receipt['body_sha256']}.body.gz"
                # Different Python/zlib versions can produce distinct gzip
                # headers/streams for identical original response bytes. Keep
                # the earliest stored file and verify its original contents.
                immutable_bytes(path, gzip.compress(body, compresslevel=9, mtime=0),
                                equivalent=lambda existing: gzip.decompress(existing) == body)
                receipt["body_path"] = path.relative_to(self.root).as_posix()
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    receipt["transport_error"] = "invalid_json"
        except requests.RequestException as exc:
            receipt["received_at"] = timestamp()
            receipt["transport_error"] = type(exc).__name__
        receipt_path = self.archive / "receipts" / f"{digest_json(receipt)}.json"
        receipt["receipt_path"] = receipt_path.relative_to(self.root).as_posix()
        immutable_json(receipt_path, receipt)
        with self.lock:
            self.receipts.append(receipt)
        return {"payload": payload, "receipt": receipt}


def load_envelope(root: Path, receipt_path: str):
    """Verify an original archived body before a comparator cache can use it."""
    root = root.resolve()
    path = (root / receipt_path).resolve()
    if not path.is_relative_to(root / "data/runtime/weather_revisions/receipts"):
        raise ValueError("Receipt outside archive")
    receipt = json.loads(path.read_text())
    expected = digest_json({k: v for k, v in receipt.items() if k != "receipt_path"})
    if receipt.get("receipt_path") != receipt_path or path.name != expected + ".json":
        raise ValueError("Archived receipt hash mismatch")
    body_path = (root / receipt["body_path"]).resolve()
    if not body_path.is_relative_to(root / "data/runtime/weather_revisions/bodies"):
        raise ValueError("Body outside archive")
    body = gzip.decompress(body_path.read_bytes())
    if hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
        raise ValueError("Archived body hash mismatch")
    return {"payload": json.loads(body), "receipt": receipt}
