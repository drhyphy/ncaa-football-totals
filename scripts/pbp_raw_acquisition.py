"""Frozen, explicit acquisition of seven public ESPN PBP Parquets.

Default invocation is a no-network preview. --download requires the plan, this
script and its tests to match committed HEAD bytes. Only Parquet footer/schema
metadata is inspected; no table columns, values, outcomes or statistics are read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyarrow.parquet as pq
import requests

VERSION = "espn-pbp-raw-acquisition-v1"
ARCHIVE = Path("model/data/raw/pbp_research/espn")
SOURCE_FILES = (
    "model/reports/PBP_RAW_ACQUISITION_PLAN.md",
    "scripts/pbp_raw_acquisition.py",
    "scripts/tests/test_pbp_raw_acquisition.py",
)
BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/"
SOURCES = tuple({"season": season, "filename": f"play_by_play_{season}.parquet",
    "url": BASE + f"play_by_play_{season}.parquet",
    "expected_sha256": "7889db9cc0c3d27651dfeb602a35e36f6b180c440bea253332b57649992f848e" if season == 2025 else None}
    for season in range(2019, 2026))
FILE_CAP = 100 * 1024 * 1024
TOTAL_CAP = 600 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 30
HEADERS = ("date", "etag", "last-modified", "content-type", "content-length",
           "content-encoding", "accept-ranges", "retry-after")


class AcquisitionError(ValueError):
    """A stable, credential-safe failure reason."""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            h.update(block)
    return h.hexdigest()


def immutable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical(value)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise AcquisitionError("immutable_record_conflict")
        return
    with path.open("xb") as stream:
        stream.write(content)


def definition():
    return {"schema_version": VERSION, "sources": list(SOURCES),
        "file_cap_bytes": FILE_CAP, "total_observed_payload_cap_bytes": TOTAL_CAP,
        "chunk_size_bytes": CHUNK_SIZE, "timeout_seconds": TIMEOUT,
        "max_redirects_per_logical_fetch": MAX_REDIRECTS,
        "max_logical_fetches_per_invocation": 7,
        "audit_scope": "Parquet footer/schema, row count and row-group metadata only",
        "historical_availability_proof": False}


def committed_provenance(root):
    """Reject uncommitted/modified source, and execution from another checkout."""
    root = Path(root).resolve()
    if Path(__file__).resolve() != root / SOURCE_FILES[1]:
        raise AcquisitionError("executed_script_is_not_selected_checkout")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
        hashes = {}
        for relative in SOURCE_FILES:
            path = root / relative
            frozen = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=root, stderr=subprocess.DEVNULL)
            if path.is_symlink() or not path.is_file() or path.read_bytes() != frozen:
                raise AcquisitionError("source_not_identical_to_committed_head")
            hashes[relative] = digest(frozen)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise AcquisitionError("plan_code_and_tests_must_be_committed") from exc
    return {"git_commit": commit, "source_files_sha256": hashes}


def public_url(url, source_url):
    """Validate redirects before requesting; never persist signed query strings."""
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise AcquisitionError("unsafe_redirect_url")
    if p.hostname == "github.com":
        if p.path != urlsplit(source_url).path or p.query:
            raise AcquisitionError("unexpected_github_path")
    elif p.hostname not in {"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}:
        raise AcquisitionError("unapproved_redirect_host")
    return {"url": urlunsplit((p.scheme, p.netloc, p.path, "", "")), "query_omitted": bool(p.query)}


def parquet_metadata(path):
    """Read no row data or min/max/value statistics."""
    parquet = pq.ParquetFile(path)
    try:
        meta = parquet.metadata
        return {"num_rows": meta.num_rows, "num_row_groups": meta.num_row_groups,
            "num_columns": meta.num_columns,
            "schema": [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in parquet.schema_arrow],
            "row_groups": [{"index": i, "num_rows": meta.row_group(i).num_rows,
                "total_byte_size": meta.row_group(i).total_byte_size} for i in range(meta.num_row_groups)]}
    finally:
        parquet.close()


def _relative(root, path):
    return Path(path).relative_to(root).as_posix()


def _number(directory, suffix):
    return 1 + max((int(p.name.split(".")[0]) for p in directory.glob("[0-9]*." + suffix)), default=0)


def _existing(root, archive):
    """Validate original completed bodies/receipts; never trust a bare filename."""
    records, observed = {}, 0
    for source in SOURCES:
        directory = archive / "attempts" / str(source["season"])
        completed = []
        for request_path in sorted(directory.glob("*.request.json")):
            request = json.loads(request_path.read_bytes())
            if request.get("source") != source or request.get("schema_version") != VERSION:
                raise AcquisitionError("stored_request_identity_mismatch")
            receipt_path = request_path.with_name(request_path.name.replace(".request.json", ".receipt.json"))
            partial = archive / "partials" / str(source["season"]) / (request_path.name.split(".")[0] + ".parquet")
            if not receipt_path.exists():
                # A terminated process has no invented receipt time. Retained
                # partial bytes still consume the lifetime payload budget.
                observed += partial.stat().st_size if partial.exists() else 0
                continue
            receipt = json.loads(receipt_path.read_bytes())
            if receipt.get("request_sha256") != file_digest(request_path) or receipt.get("source") != source:
                raise AcquisitionError("stored_receipt_identity_mismatch")
            count = receipt.get("bytes_observed")
            stored = receipt.get("bytes_stored")
            if (type(count) is not int or type(stored) is not int or count < stored
                or stored < 0 or stored > FILE_CAP):
                raise AcquisitionError("invalid_stored_byte_count")
            observed += count
            if receipt.get("status") not in ("complete", "failed"):
                raise AcquisitionError("invalid_stored_receipt_status")
            if receipt.get("status") == "complete":
                completed.append(receipt)
            elif receipt.get("body_path"):
                if (receipt["body_path"] != _relative(root, partial) or not partial.is_file()
                    or partial.is_symlink() or partial.stat().st_size != stored
                    or file_digest(partial) != receipt.get("sha256")):
                    raise AcquisitionError("failed_partial_integrity_mismatch")
            elif stored != 0 or receipt.get("sha256") is not None:
                raise AcquisitionError("missing_stored_partial")
        destination = archive / source["filename"]
        if len(completed) > 1:
            raise AcquisitionError("duplicate_completed_source")
        if completed:
            record = completed[0]
            if (not destination.is_file() or destination.is_symlink()
                or destination.stat().st_size != record.get("bytes_stored")
                or file_digest(destination) != record.get("sha256")
                or record.get("body_path") != _relative(root, destination)
                or (source["expected_sha256"] and record["sha256"] != source["expected_sha256"])
                or parquet_metadata(destination) != record.get("parquet_metadata")):
                raise AcquisitionError("completed_file_integrity_mismatch")
            records[source["season"]] = record
        elif destination.exists():
            raise AcquisitionError("unreceipted_existing_file")
    return records, observed


def fetch_one(root, archive, source, session, remaining, provenance):
    """One logical fetch, with bounded validated redirects and no retries."""
    attempts = archive / "attempts" / str(source["season"])
    attempts.mkdir(parents=True, exist_ok=True)
    number = _number(attempts, "request.json")
    request_path = attempts / f"{number:04d}.request.json"
    receipt_path = attempts / f"{number:04d}.receipt.json"
    partial = archive / "partials" / str(source["season"]) / f"{number:04d}.parquet"
    partial.parent.mkdir(parents=True, exist_ok=True)
    request = {"schema_version": VERSION, "source": source, "requested_at": utcnow(), **provenance}
    immutable_json(request_path, request)
    receipt = {"schema_version": VERSION, "source": source,
        "request_path": _relative(root, request_path), "request_sha256": file_digest(request_path),
        "requested_at": request["requested_at"], "received_at": None,
        "receipt_path": _relative(root, receipt_path), "status": "failed",
        "status_code": None, "redirect_chain": [], "bytes_observed": 0,
        "bytes_stored": 0, "sha256": None, "body_path": None, "error": None}
    response = None
    body_hash = hashlib.sha256()
    try:
        url = source["url"]
        for index in range(MAX_REDIRECTS + 1):
            safe = public_url(url, source["url"])
            requested_at = utcnow()
            hop = {**safe, "requested_at": requested_at, "headers_received_at": None,
                   "status_code": None, "response_headers": {}}
            receipt["redirect_chain"].append(hop)
            response = session.get(url, stream=True, allow_redirects=False, timeout=(TIMEOUT, TIMEOUT),
                headers={"Accept": "application/octet-stream", "Accept-Encoding": "identity", "User-Agent": VERSION})
            headers = {k.lower(): str(v) for k, v in response.headers.items()}
            receipt["status_code"] = int(response.status_code)
            hop.update(headers_received_at=utcnow(), status_code=int(response.status_code),
                response_headers={k: headers[k] for k in HEADERS if k in headers})
            if response.status_code in (301, 302, 303, 307, 308):
                if index == MAX_REDIRECTS or not headers.get("location"):
                    raise AcquisitionError("redirect_limit_or_missing_location")
                url = urljoin(url, headers["location"])
                public_url(url, source["url"])
                response.close()
                response = None
                continue
            if response.status_code != 200:
                raise AcquisitionError("http_status_" + str(response.status_code))
            break
        if headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            raise AcquisitionError("unexpected_content_encoding")
        length = headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                raise AcquisitionError("invalid_content_length")
            length = int(length)
            if length > min(FILE_CAP, remaining):
                raise AcquisitionError("declared_payload_exceeds_cap")
        with partial.open("xb") as output:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                receipt["bytes_observed"] += len(chunk)
                if receipt["bytes_observed"] > min(FILE_CAP, remaining):
                    raise AcquisitionError("streamed_payload_exceeds_cap")
                output.write(chunk)
                body_hash.update(chunk)
                receipt["bytes_stored"] += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if length is not None and receipt["bytes_stored"] != length:
            raise AcquisitionError("content_length_mismatch")
        if source["expected_sha256"] and body_hash.hexdigest() != source["expected_sha256"]:
            raise AcquisitionError("expected_sha256_mismatch")
        metadata = parquet_metadata(partial)
        destination = archive / source["filename"]
        # Exclusive hard-link publication cannot overwrite an existing file.
        os.link(partial, destination)
        partial.unlink()
        receipt.update(status="complete", body_path=_relative(root, destination), parquet_metadata=metadata)
    except (Exception, KeyboardInterrupt) as exc:
        receipt["error"] = str(exc) if isinstance(exc, AcquisitionError) else type(exc).__name__
        if partial.exists():
            receipt["body_path"] = _relative(root, partial)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception as exc:
                # A close error must not suppress the original attempt receipt.
                receipt["response_close_error"] = type(exc).__name__
        receipt["sha256"] = body_hash.hexdigest() if receipt["body_path"] else None
        receipt["received_at"] = utcnow()
        immutable_json(receipt_path, receipt)
    return receipt


def acquire(root, *, download=False, resume=False, session=None):
    root = Path(root).resolve()
    spec = definition()
    if not download:
        if resume:
            raise AcquisitionError("resume_requires_download")
        return {"status": "preview", "http_requests": 0, "definition": spec}
    provenance = committed_provenance(root)  # Must precede any HTTP or session creation.
    archive = root / ARCHIVE
    if any(path.is_symlink() for path in (archive, *archive.parents)):
        raise AcquisitionError("archive_symlink_not_allowed")
    if archive.exists() and any(path.is_symlink() for path in archive.rglob("*")):
        raise AcquisitionError("archive_symlink_not_allowed")
    plan_path = archive / "acquisition_plan.json"
    if plan_path.exists():
        original = json.loads(plan_path.read_bytes())
        if original.get("definition") != spec or original.get("source_files_sha256") != provenance["source_files_sha256"]:
            raise AcquisitionError("frozen_acquisition_plan_mismatch")
        existing, observed = _existing(root, archive)
        if len(existing) != len(SOURCES) and not resume:
            raise AcquisitionError("incomplete_archive_requires_explicit_resume")
    else:
        if resume:
            raise AcquisitionError("resume_requires_original_acquisition")
        if archive.exists() and any(archive.iterdir()):
            raise AcquisitionError("unplanned_archive_contents")
        immutable_json(plan_path, {"definition": spec, "created_at": utcnow(), **provenance})
        existing, observed = {}, 0
    runs = archive / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_path = runs / f"{_number(runs, 'json'):04d}.json"
    result = {"schema_version": VERSION, "started_at": utcnow(), "finished_at": None,
        "status": "complete", "resume": bool(resume), "plan_path": _relative(root, plan_path),
        "plan_sha256": file_digest(plan_path), **provenance, "logical_fetches": 0,
        "http_requests": 0, "prior_bytes_observed": observed, "total_bytes_observed": observed,
        "completed_seasons": sorted(existing), "new_receipts": [], "error": None}
    owned_session = session is None
    try:
        if session is None and len(existing) < len(SOURCES):
            session = requests.Session()
            session.trust_env = False  # No netrc credentials or environment proxies.
            session.auth = None
        for source in SOURCES:
            if source["season"] in existing:
                continue
            if result["total_bytes_observed"] >= TOTAL_CAP:
                raise AcquisitionError("lifetime_payload_cap_reached")
            if hasattr(session, "cookies"):
                session.cookies.clear()
            receipt = fetch_one(root, archive, source, session,
                TOTAL_CAP - result["total_bytes_observed"], provenance)
            result["logical_fetches"] += 1
            result["http_requests"] += len(receipt["redirect_chain"])
            result["total_bytes_observed"] += receipt["bytes_observed"]
            result["new_receipts"].append({"path": receipt["receipt_path"],
                "sha256": file_digest(root / receipt["receipt_path"]), "season": source["season"], "status": receipt["status"]})
            if receipt["status"] != "complete":
                raise AcquisitionError(receipt["error"] or "source_fetch_failed")
            result["completed_seasons"].append(source["season"])
    except (Exception, KeyboardInterrupt) as exc:
        result["status"] = "partial"
        result["error"] = str(exc) if isinstance(exc, AcquisitionError) else type(exc).__name__
    finally:
        if owned_session and session is not None:
            session.close()
        result["finished_at"] = utcnow()
        result["completed_seasons"].sort()
        result["missing_seasons"] = [s["season"] for s in SOURCES if s["season"] not in result["completed_seasons"]]
        immutable_json(run_path, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--download", action="store_true", help="Explicitly acquire the committed seven-file plan")
    parser.add_argument("--resume", action="store_true", help="Explicitly attempt only missing/failed files in the same original archive")
    args = parser.parse_args(argv)
    try:
        result = acquire(args.root, download=args.download, resume=args.resume)
    except AcquisitionError as exc:
        result = {"status": "blocked", "error": str(exc)}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result["status"] in ("preview", "complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
