"""Offline original-byte, cap, provenance and metadata-only acquisition tests."""
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "pbp_raw_acquisition.py"
spec = importlib.util.spec_from_file_location("pbp_raw_acquisition", SCRIPT)
acq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acq)


class Response:
    def __init__(self, body=b"", status=200, headers=None, chunks=None):
        self.body, self.status_code = body, status
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
        self.chunks = chunks
        self.consumed = False
        self.closed = False

    def iter_content(self, chunk_size):
        self.consumed = True
        assert chunk_size == acq.CHUNK_SIZE
        yield from self.chunks if self.chunks is not None else [self.body]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (30, 30)
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        assert not any(k.lower() == "authorization" for k in kwargs["headers"])
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def parquet():
    stream = io.BytesIO()
    pq.write_table(pa.table({"synthetic_id": [1, 2], "synthetic_clock": ["1:00", "0:30"]}), stream, row_group_size=1)
    return stream.getvalue()


@pytest.fixture
def frozen(monkeypatch, parquet):
    provenance = {"git_commit": "a" * 40, "source_files_sha256": {p: "b" * 64 for p in acq.SOURCE_FILES}}
    monkeypatch.setattr(acq, "committed_provenance", lambda root: provenance)
    # Tiny synthetic bytes substitute solely for the release-body hash in tests.
    sources = [{**s, "expected_sha256": hashlib.sha256(parquet).hexdigest() if s["season"] == 2025 else None} for s in acq.SOURCES]
    monkeypatch.setattr(acq, "SOURCES", tuple(sources))
    return provenance


def receipts(root):
    return [json.loads(p.read_bytes()) for p in sorted((root / acq.ARCHIVE / "attempts").glob("*/*.receipt.json"))]


def test_exact_frozen_sources_and_reported_2025_pin():
    assert [s["season"] for s in acq.SOURCES] == list(range(2019, 2026))
    assert all(s["url"] == acq.BASE + s["filename"] for s in acq.SOURCES)
    assert all(s["expected_sha256"] is None for s in acq.SOURCES[:-1])
    assert acq.SOURCES[-1]["expected_sha256"] == "7889db9cc0c3d27651dfeb602a35e36f6b180c440bea253332b57649992f848e"
    assert acq.FILE_CAP == 104857600 and acq.TOTAL_CAP == 629145600


def test_preview_issues_zero_http_and_writes_nothing(tmp_path):
    session = Session()
    result = acq.acquire(tmp_path, session=session)
    assert result["status"] == "preview" and result["http_requests"] == 0
    assert not session.calls and not list(tmp_path.iterdir())


def test_uncommitted_source_blocks_before_http_or_archive(tmp_path):
    session = Session()
    with pytest.raises(acq.AcquisitionError, match="selected_checkout"):
        acq.acquire(tmp_path, download=True, session=session)
    assert not session.calls and not list(tmp_path.iterdir())


def test_commit_guard_requires_all_tracked_identical_bytes(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")
    for relative in acq.SOURCE_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic frozen source\n")
    monkeypatch.setattr(acq, "__file__", str(tmp_path / acq.SOURCE_FILES[1]))
    with pytest.raises(acq.AcquisitionError, match="must_be_committed"):
        acq.committed_provenance(tmp_path)
    git("add", *acq.SOURCE_FILES)
    git("commit", "-m", "Freeze synthetic source fixture")
    result = acq.committed_provenance(tmp_path)
    assert len(result["git_commit"]) == 40 and len(result["source_files_sha256"]) == 3
    (tmp_path / acq.SOURCE_FILES[0]).write_text("changed\n")
    with pytest.raises(acq.AcquisitionError, match="not_identical"):
        acq.committed_provenance(tmp_path)


def test_seven_successes_keep_original_bytes_and_metadata_only(tmp_path, frozen, parquet, monkeypatch):
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: pytest.fail("No table values may be read"))
    session = Session([Response(parquet) for _ in range(7)])
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["status"] == "complete" and result["logical_fetches"] == 7
    assert result["http_requests"] == 7 and result["total_bytes_observed"] == 7 * len(parquet)
    assert result["missing_seasons"] == []
    for record in receipts(tmp_path):
        body = tmp_path / record["body_path"]
        assert body.read_bytes() == parquet
        assert record["sha256"] == hashlib.sha256(parquet).hexdigest()
        meta = record["parquet_metadata"]
        assert meta["num_rows"] == 2 and meta["num_row_groups"] == 2
        assert [f["name"] for f in meta["schema"]] == ["synthetic_id", "synthetic_clock"]
        assert "statistics" not in json.dumps(meta) and "1:00" not in json.dumps(meta)
        assert datetime.fromisoformat(record["requested_at"]) <= datetime.fromisoformat(record["received_at"])
        assert not str(tmp_path) in json.dumps(record)


def test_completed_cache_revalidates_and_never_refetches(tmp_path, frozen, parquet):
    acq.acquire(tmp_path, download=True, session=Session([Response(parquet) for _ in range(7)]))
    before = {p: p.read_bytes() for p in (tmp_path / acq.ARCHIVE).glob("play_by_play_*.parquet")}
    session = Session()
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["status"] == "complete" and result["logical_fetches"] == 0
    assert not session.calls and len(receipts(tmp_path)) == 7
    assert before == {p: p.read_bytes() for p in before}


def test_corrupted_completed_file_fails_before_resume_http(tmp_path, frozen, parquet):
    acq.acquire(tmp_path, download=True, session=Session([Response(parquet) for _ in range(7)]))
    (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").write_bytes(b"corrupt")
    session = Session()
    with pytest.raises(acq.AcquisitionError, match="integrity_mismatch"):
        acq.acquire(tmp_path, download=True, resume=True, session=session)
    assert not session.calls


def test_failure_stops_then_explicit_resume_only_missing(tmp_path, frozen, parquet):
    error = Response(b"unconsumed error body", status=503)
    first = acq.acquire(tmp_path, download=True, session=Session([Response(parquet), error]))
    assert first["status"] == "partial" and first["logical_fetches"] == 2
    assert first["completed_seasons"] == [2019] and not error.consumed
    original = (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").read_bytes()
    with pytest.raises(acq.AcquisitionError, match="explicit_resume"):
        acq.acquire(tmp_path, download=True, session=Session())
    session = Session([Response(parquet) for _ in range(6)])
    resumed = acq.acquire(tmp_path, download=True, resume=True, session=session)
    assert resumed["status"] == "complete" and resumed["logical_fetches"] == 6
    assert all("2019" not in url for url, _ in session.calls)
    assert (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").read_bytes() == original
    assert len(receipts(tmp_path)) == 8


@pytest.mark.parametrize("options,reason", [({"resume": True}, "resume_requires_download"),
    ({"download": True, "resume": True}, "resume_requires_original")])
def test_resume_requires_original_and_explicit_download(tmp_path, frozen, options, reason):
    session = Session()
    with pytest.raises(acq.AcquisitionError, match=reason):
        acq.acquire(tmp_path, session=session, **options)
    assert not session.calls


def test_oversized_declared_body_not_consumed(tmp_path, frozen):
    response = Response(headers={"Content-Length": str(acq.FILE_CAP + 1)})
    result = acq.acquire(tmp_path, download=True, session=Session([response]))
    assert result["error"] == "declared_payload_exceeds_cap" and not response.consumed
    assert receipts(tmp_path)[0]["bytes_observed"] == 0


def test_stream_cap_crossing_chunk_is_charged_but_not_stored(tmp_path, frozen, monkeypatch):
    monkeypatch.setattr(acq, "FILE_CAP", 10)
    response = Response(chunks=[b"123456", b"789012"])
    response.headers = {}
    result = acq.acquire(tmp_path, download=True, session=Session([response]))
    record = receipts(tmp_path)[0]
    assert result["error"] == "streamed_payload_exceeds_cap"
    assert record["bytes_observed"] == 12 and record["bytes_stored"] == 6
    assert (tmp_path / record["body_path"]).read_bytes() == b"123456"
    assert not (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").exists()


def test_lifetime_cap_includes_previously_failed_bytes(tmp_path, frozen, parquet, monkeypatch):
    monkeypatch.setattr(acq, "TOTAL_CAP", len(parquet) + 5)
    # First failed attempt still consumed a whole valid Parquet body.
    response = Response(parquet, headers={"Content-Length": str(len(parquet) - 1)})
    first = acq.acquire(tmp_path, download=True, session=Session([response]))
    assert first["total_bytes_observed"] == len(parquet)
    second_response = Response(parquet)
    second = acq.acquire(tmp_path, download=True, resume=True, session=Session([second_response]))
    assert second["error"] == "declared_payload_exceeds_cap" and not second_response.consumed
    assert second["prior_bytes_observed"] == len(parquet)


@pytest.mark.parametrize("body,headers,error", [
    (b"bad", {"Content-Length": "4"}, "content_length_mismatch"),
    (b"bad", {"Content-Length": "many"}, "invalid_content_length"),
    (b"bad", {"Content-Encoding": "gzip"}, "unexpected_content_encoding"),
    (b"bad", {}, "ArrowInvalid"),
])
def test_invalid_response_stays_failed_not_input(tmp_path, frozen, body, headers, error):
    result = acq.acquire(tmp_path, download=True, session=Session([Response(body, headers=headers)]))
    assert result["status"] == "partial" and result["error"] == error
    assert not (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").exists()


def test_expected_hash_mismatch_never_publishes(tmp_path, frozen, parquet, monkeypatch):
    monkeypatch.setattr(acq, "SOURCES", ({**acq.SOURCES[0], "expected_sha256": "0" * 64},))
    result = acq.acquire(tmp_path, download=True, session=Session([Response(parquet)]))
    assert result["error"] == "expected_sha256_mismatch"
    assert not (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").exists()


def test_safe_redirect_records_receipts_without_signed_query(tmp_path, frozen, parquet):
    signed = "https://release-assets.githubusercontent.com/public/asset?jwt=DO_NOT_PERSIST"
    response = Response(status=302, headers={"Location": signed, "Set-Cookie": "DO_NOT_PERSIST"})
    session = Session([response, *[Response(parquet) for _ in range(7)]])
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["status"] == "complete" and result["http_requests"] == 8
    record = receipts(tmp_path)[0]
    assert record["redirect_chain"][1]["query_omitted"] is True
    assert "DO_NOT_PERSIST" not in json.dumps(record)
    assert not response.consumed and response.closed


@pytest.mark.parametrize("url", ["http://release-assets.githubusercontent.com/a",
    "https://github.com/another/repository", "https://release-assets.githubusercontent.com.evil.invalid/a",
    "https://u:secret@release-assets.githubusercontent.com/a", "https://example.org/a",
    "https://release-assets.githubusercontent.com:invalid/a?secret=DO_NOT_PERSIST"])
def test_bad_redirect_rejected_before_follow(tmp_path, frozen, url):
    session = Session([Response(status=302, headers={"Location": url})])
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["status"] == "partial" and len(session.calls) == 1
    assert url not in json.dumps(receipts(tmp_path))


def test_redirect_chain_bounded_without_retry(tmp_path, frozen):
    session = Session([Response(status=302, headers={"Location": acq.SOURCES[0]["url"]}) for _ in range(4)])
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["error"] == "redirect_limit_or_missing_location" and len(session.calls) == 4
    assert result["logical_fetches"] == 1


def test_transport_failure_is_counted_and_error_text_not_leaked(tmp_path, frozen):
    session = Session([requests.ConnectionError("https://host.invalid/?secret=DO_NOT_PERSIST")])
    result = acq.acquire(tmp_path, download=True, session=session)
    assert result["http_requests"] == 1 and result["logical_fetches"] == 1
    assert result["error"] == "ConnectionError" and "DO_NOT_PERSIST" not in json.dumps(receipts(tmp_path))


def test_response_close_error_cannot_suppress_failure_receipt(tmp_path, frozen):
    response = Response(status=500)
    def close():
        raise requests.ConnectionError("DO_NOT_PERSIST")
    response.close = close
    result = acq.acquire(tmp_path, download=True, session=Session([response]))
    record = receipts(tmp_path)[0]
    assert result["error"] == "http_status_500"
    assert record["response_close_error"] == "ConnectionError"
    assert "DO_NOT_PERSIST" not in json.dumps(record)


def test_failed_partial_corruption_is_not_silently_resumed(tmp_path, frozen):
    acq.acquire(tmp_path, download=True, session=Session([Response(b"invalid parquet")]))
    record = receipts(tmp_path)[0]
    (tmp_path / record["body_path"]).write_bytes(b"changed")
    with pytest.raises(acq.AcquisitionError, match="partial_integrity"):
        acq.acquire(tmp_path, download=True, resume=True, session=Session())


def test_bare_completed_filename_rejected(tmp_path, frozen):
    acq.acquire(tmp_path, download=True, session=Session([Response(status=500)]))
    (tmp_path / acq.ARCHIVE / "play_by_play_2019.parquet").write_bytes(b"unreceipted")
    with pytest.raises(acq.AcquisitionError, match="unreceipted"):
        acq.acquire(tmp_path, download=True, resume=True, session=Session())


def test_changed_definition_blocks_resume(tmp_path, frozen, monkeypatch):
    acq.acquire(tmp_path, download=True, session=Session([Response(status=500)]))
    monkeypatch.setattr(acq, "FILE_CAP", acq.FILE_CAP - 1)
    with pytest.raises(acq.AcquisitionError, match="plan_mismatch"):
        acq.acquire(tmp_path, download=True, resume=True, session=Session())


def test_archive_symlink_rejected_before_http(tmp_path, frozen):
    archive = tmp_path / acq.ARCHIVE
    archive.mkdir(parents=True)
    (archive / "attempts").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(acq.AcquisitionError, match="symlink"):
        acq.acquire(tmp_path, download=True, session=Session())


def test_immutable_json_refuses_conflicting_overwrite(tmp_path):
    path = tmp_path / "record.json"
    acq.immutable_json(path, {"fixed": 1})
    acq.immutable_json(path, {"fixed": 1})
    with pytest.raises(acq.AcquisitionError, match="immutable"):
        acq.immutable_json(path, {"fixed": 2})


def test_cli_partial_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(acq, "acquire", lambda *a, **k: {"status": "partial", "error": "http_status_503"})
    assert acq.main(["--root", str(tmp_path), "--download"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "partial"
